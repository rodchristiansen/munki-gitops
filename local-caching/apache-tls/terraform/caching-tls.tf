# One self-signed certificate per local caching server.
#
# Each certificate names its own server and nothing else, and is not a CA, so
# a Mac that trusts it trusts exactly that host. There is deliberately no
# private root: a root trusted by every Mac can sign a certificate for any
# name, and its key would sit in Terraform state.
#
# Push each server's certificate to the Macs that use it with a certificate
# profile (the caching_server_cert_pem output). Each server pulls its own
# certificate and key, and the repo's Basic Auth credential, from Key Vault
# with apache-tls-sync.sh, and can read no other server's key.
#
# The private keys are in Terraform state. Keep the state in a backend with
# encryption at rest and RBAC limited to the identity that applies this, and
# treat read access to it as the ability to impersonate a caching server.
#
# Rotation: each certificate is valid for 397 days and re-issues on the first
# apply inside its last 30. A re-issued certificate is a new trust anchor, so
# the profile must carry it before the server starts presenting it. See the
# README.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

variable "caching_servers" {
  description = "Each caching server's DNS name, mapped to the object id of the service principal it signs in as"
  type        = map(string)
  default = {
    "mirror1.example.org" = "00000000-0000-0000-0000-000000000001"
    "mirror2.example.org" = "00000000-0000-0000-0000-000000000002"
  }
}

variable "organization" {
  type    = string
  default = "Example Org"
}

variable "key_vault_id" {
  description = "Key Vault that already holds the repo's Basic Auth secrets"
  type        = string
}

variable "repo_auth_secret_names" {
  description = "Names of the Basic Auth username and password secrets in that vault"
  type        = list(string)
  default     = ["MunkiUsername", "MunkiPassword"]
}

locals {
  # Key Vault secret names allow letters, digits and dashes only.
  secret_suffix = { for host, _ in var.caching_servers : host => replace(host, ".", "-") }

  # Every server reads the shared credential, and only its own certificate and
  # key.
  server_secret_reads = merge(
    { for pair in setproduct(keys(var.caching_servers), var.repo_auth_secret_names) :
    "${pair[0]}/${pair[1]}" => { host = pair[0], secret = pair[1] } },
    { for host, _ in var.caching_servers :
    "${host}/cert" => { host = host, secret = "CachingServerCertificate-${local.secret_suffix[host]}" } },
    { for host, _ in var.caching_servers :
    "${host}/key" => { host = host, secret = "CachingServerKey-${local.secret_suffix[host]}" } },
  )
}

resource "tls_private_key" "caching_server" {
  for_each    = var.caching_servers
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "caching_server" {
  for_each              = var.caching_servers
  private_key_pem       = tls_private_key.caching_server[each.key].private_key_pem
  is_ca_certificate     = false
  dns_names             = [each.key]
  validity_period_hours = 9528
  early_renewal_hours   = 720
  allowed_uses          = ["digital_signature", "server_auth"]

  subject {
    common_name  = each.key
    organization = var.organization
  }
}

resource "azurerm_key_vault_secret" "caching_server_cert" {
  for_each     = var.caching_servers
  name         = "CachingServerCertificate-${local.secret_suffix[each.key]}"
  value        = tls_self_signed_cert.caching_server[each.key].cert_pem
  key_vault_id = var.key_vault_id
  content_type = "application/x-pem-file"
}

resource "azurerm_key_vault_secret" "caching_server_key" {
  for_each     = var.caching_servers
  name         = "CachingServerKey-${local.secret_suffix[each.key]}"
  value        = tls_private_key.caching_server[each.key].private_key_pem
  key_vault_id = var.key_vault_id
  content_type = "application/x-pem-file"
}

# Read access scoped secret by secret, not to the whole vault, which usually
# also holds signing and deployment material.
resource "azurerm_role_assignment" "caching_server_secrets" {
  for_each = local.server_secret_reads

  scope                = "${var.key_vault_id}/secrets/${each.value.secret}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = var.caching_servers[each.value.host]
  principal_type       = "ServicePrincipal"

  depends_on = [azurerm_key_vault_secret.caching_server_cert, azurerm_key_vault_secret.caching_server_key]
}

output "caching_server_cert_pem" {
  description = "Each server's certificate, for the certificate profile the Macs that use it receive"
  value       = { for host, cert in tls_self_signed_cert.caching_server : host => cert.cert_pem }
}
