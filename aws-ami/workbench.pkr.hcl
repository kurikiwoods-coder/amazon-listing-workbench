packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = ">= 1.3.0, < 2.0.0"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "build_subnet_id" {
  type        = string
  description = "A public IPv4 subnet for the temporary image builder."
  validation {
    condition     = can(regex("^subnet-[0-9a-f]+$", var.build_subnet_id))
    error_message = "Enter a valid image-builder subnet ID."
  }
}

variable "install_playwright" {
  type        = bool
  default     = false
  description = "Install Chromium and enable authorized browser collection on the instance."
}

source "amazon-ebs" "workbench" {
  ami_name                                 = "listing-workbench-1.3.2-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  ami_description                          = "Listing Studio 1.3.2 on Ubuntu 24.04. Local port 8000 via Session Manager."
  region                                   = var.aws_region
  subnet_id                                = var.build_subnet_id
  instance_type                            = "t3.medium"
  associate_public_ip_address              = true
  temporary_security_group_source_public_ip = true
  ssh_username                             = "ubuntu"
  ssh_timeout                              = "12m"
  ssh_clear_authorized_keys                = true
  ssh_disable_agent_forwarding             = true
  encrypt_boot                             = true
  imds_support                             = "v2.0"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
      architecture        = "x86_64"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    owners      = ["099720109477"]
    most_recent = true
  }

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 16
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Application = "Listing Studio"
    Version     = "1.3.2"
  }
}

build {
  sources = ["source.amazon-ebs.workbench"]

  provisioner "file" {
    source      = "${path.root}/workbench-runtime.tar.gz"
    destination = "/tmp/listing-workbench-runtime.tar.gz"
  }

  provisioner "file" {
    source      = "${path.root}/listing-workbench.service"
    destination = "/tmp/listing-workbench.service"
  }

  provisioner "shell" {
    environment_vars = ["INSTALL_PLAYWRIGHT=${var.install_playwright}"]
    script           = "${path.root}/install.sh"
  }

  post-processor "manifest" {
    output     = "${path.root}/manifest.json"
    strip_path = true
  }
}
