// Shared tflint config. The Makefile runs tflint once per Terraform root and
// passes this file explicitly, since --chdir would otherwise look for a
// config inside each root.

config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

// Every taggable resource carries the project tag (provider default_tags count).
rule "aws_resource_missing_tags" {
  enabled = true
  tags    = ["project"]
}

rule "terraform_naming_convention" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}
