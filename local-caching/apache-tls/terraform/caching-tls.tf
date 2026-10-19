# A private root and one server certificate for the local caching servers.
#
# The certificate only has to be trusted by your own managed Macs, so it comes
# from a private root rather than a public CA: no DNS validation, no renewal
# client on the servers, no public record of the host names. Push the root to
# Macs with a certificate profile (the caching_root_cert_pem output); the
# servers pull the leaf, its key and the repo's Basic Auth credential from Key
# Vault with apache-tls-sync.sh.
#
# Rotation: the leaf re-issues on the first apply inside its last 90 days, and
# the servers pick it up on their next daily sync. The root does not change, so
# the profile does not either. Apple rejects TLS leaves valid for more than 825
# days.
#
# The root's private key lives in Terraform state. Keep the state in a backend
# with encryption and access control, and treat read access to it as the
# ability to issue certificates your Macs trust.

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

variable "caching_server_hosts" {
  description = "DNS names clients use for the caching servers; the first is the common name"
  type        = list(string)
  default     = ["mirror1.example.org", "mirror2.example.org"]
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

variable "caching_servers_principal_id" {
  description = "Object id of the service principal the caching servers sign in as"
  type        = string
}

resource "tls_private_key" "caching_root" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "tls_self_signed_cert" "caching_root" {
  private_key_pem       = tls_private_key.caching_root.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 87600
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]

  subject {
    common_name  = "${var.organization} Servers Root CA"
    organization = var.organization
  }
}

resource "tls_private_key" "caching_server" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "caching_server" {
  private_key_pem = tls_private_key.caching_server.private_key_pem
  dns_names       = var.caching_server_hosts

  subject {
    common_name  = var.caching_server_hosts[0]
    organization = var.organization
  }
}

resource "tls_locally_signed_cert" "caching_server" {
  cert_request_pem      = tls_cert_request.caching_server.cert_request_pem
  ca_private_key_pem    = tls_private_key.caching_root.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.caching_root.cert_pem
  validity_period_hours = 19800
  early_renewal_hours   = 2160
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth"]
}

# The chain the server presents: leaf, then root.
resource "azurerm_key_vault_secret" "caching_server_cert" {
  name         = "CachingServerCertificate"
  value        = "${tls_locally_signed_cert.caching_server.cert_pem}${tls_self_signed_cert.caching_root.cert_pem}"
  key_vault_id = var.key_vault_id
  content_type = "application/x-pem-file"
}

resource "azurerm_key_vault_secret" "caching_server_key" {
  name         = "CachingServerKey"
  value        = tls_private_key.caching_server.private_key_pem
  key_vault_id = var.key_vault_id
  content_type = "application/x-pem-file"
}

# Read access scoped to the four secrets the servers need, not the whole vault,
# which usually also holds signing and deployment material.
resource "azurerm_role_assignment" "caching_servers_secrets" {
  for_each = toset(concat(var.repo_auth_secret_names, [
    azurerm_key_vault_secret.caching_server_cert.name,
    azurerm_key_vault_secret.caching_server_key.name,
  ]))

  scope                = "${var.key_vault_id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = var.caching_servers_principal_id
  principal_type       = "ServicePrincipal"
}

output "caching_root_cert_pem" {
  description = "The root to put in the certificate profile your Macs receive"
  value       = tls_self_signed_cert.caching_root.cert_pem
}
