view: premium {
  sql_table_name: bancassurance.premium ;;
  dimension: premium_id {
    primary_key: yes
    type: number
    sql: ${TABLE}.premium_id ;;
  }
  dimension: written_on {
    type: date
    sql: ${TABLE}.written_on ;;
  }
  dimension: premium_amount {
    type: number
    sql: ${TABLE}.premium_amount ;;
  }
  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }
  measure: written_premium {
    type: sum
    sql: ${TABLE}.premium_amount ;;
    label: "Written premium"
    description: "Premium written through the bancassurance channel."
    value_format_name: gbp
    filters: [status: "active"]
  }
}
