# Amplify hosting app + branch — modeled on minelogx-frontend-poc / demo.
# repository/oauth_token are optional (manual deploys leave them null, as in POC).

variable "name_prefix" {
  type = string
}

variable "branch_name" {
  type    = string
  default = "demo"
}

variable "branch_stage" {
  type    = string
  default = "PRODUCTION"
}

variable "repository" {
  description = "Git repo URL (null for manual/console deploys)."
  type        = string
  default     = null
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_amplify_app" "this" {
  name                  = "${var.name_prefix}-frontend"
  platform              = "WEB"
  repository            = var.repository
  environment_variables = var.environment_variables

  # SPA fallback (serve index.html on 404), as in the POC.
  custom_rule {
    source = "/<*>"
    status = "404-200"
    target = "/index.html"
  }

  tags = var.tags
}

resource "aws_amplify_branch" "this" {
  app_id            = aws_amplify_app.this.id
  branch_name       = var.branch_name
  stage             = var.branch_stage
  enable_auto_build = true
  tags              = var.tags
}

output "app_id" {
  value = aws_amplify_app.this.id
}

output "default_domain" {
  value = "https://${var.branch_name}.${aws_amplify_app.this.default_domain}"
}
