output "workgroup_names" {
  description = "Workgroup names."
  value       = sort(keys(aws_athena_workgroup.this))
}

output "results_locations" {
  description = "Query results location of each workgroup, keyed by workgroup name."
  value       = { for name, wg in aws_athena_workgroup.this : name => wg.configuration[0].result_configuration[0].output_location }
}
