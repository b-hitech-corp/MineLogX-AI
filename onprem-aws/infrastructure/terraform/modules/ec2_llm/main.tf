# EC2 Ollama instances — FALLBACK LLM tier.
#
# These are NOT POC-throwaway: they stay as a backup for when Amazon Bedrock is
# unavailable. Toggle per environment with `enabled`; `prevent_destroy` guards
# against accidental deletion. (CloudFormation equivalent: a Condition gating the
# resources + DeletionPolicy/UpdateReplacePolicy: Retain.)

variable "enabled" {
  description = "Whether the fallback Ollama instances exist in this environment."
  type        = bool
  default     = false
}

variable "name_prefix" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "security_group_ids" {
  type = list(string)
}

variable "key_name" {
  type    = string
  default = null
}

variable "iam_instance_profile" {
  description = "Instance profile for SSM access."
  type        = string
  default     = "AmazonSSMRoleForInstancesQuickSetup"
}

# One entry per model host. Defaults mirror the imported POC.
variable "instances" {
  description = "Map of logical name => {ami, instance_type, root_volume_gb}."
  type = map(object({
    ami            = string
    instance_type  = string
    root_volume_gb = number
  }))
  default = {
    qwen3      = { ami = "ami-09343c2dd0ee54c92", instance_type = "g5.2xlarge", root_volume_gb = 200 }
    gemma3     = { ami = "ami-09343c2dd0ee54c92", instance_type = "g5.2xlarge", root_volume_gb = 200 }
    embeddings = { ami = "ami-091138d0f0d41ff90", instance_type = "t3.large", root_volume_gb = 100 }
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_instance" "this" {
  for_each = var.enabled ? var.instances : {}

  ami                    = each.value.ami
  instance_type          = each.value.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = var.security_group_ids
  key_name               = var.key_name
  iam_instance_profile   = var.iam_instance_profile
  ebs_optimized          = true

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = each.value.root_volume_gb
    encrypted   = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-${each.key}" })

  lifecycle {
    # Backup tier — never destroy implicitly (e.g. on env teardown).
    prevent_destroy = true
    # AMI/user-data drift on a long-lived fallback box shouldn't force replace.
    ignore_changes = [ami]
  }
}

output "instance_ids" {
  value = { for k, v in aws_instance.this : k => v.id }
}

output "private_ips" {
  value = { for k, v in aws_instance.this : k => v.private_ip }
}
