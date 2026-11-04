output "database_name" {
  description = "Glue database holding the raw tables."
  value       = aws_glue_catalog_database.raw.name
}

output "table_names" {
  description = "Raw table names, one per feed."
  value       = sort([for t in aws_glue_catalog_table.raw : t.name])
}
