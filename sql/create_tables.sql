

CREATE TABLE IF NOT EXISTS `swasthya_ai.symptom_reports` (
  report_date DATE,
  district STRING,
  block STRING,
  village STRING,
  disease STRING,
  cases INT64,
  population INT64
);

CREATE TABLE IF NOT EXISTS `swasthya_ai.immunization_records` (
  report_date DATE,
  district STRING,
  block STRING,
  village STRING,
  vaccine STRING,
  children_due INT64,
  children_covered INT64
);

CREATE TABLE IF NOT EXISTS `swasthya_ai.notifications_log` (
  created_at TIMESTAMP,
  priority STRING,
  alert_summary STRING,
  recommendation STRING,
  district STRING,
  block STRING,
  village STRING,
  source_anomaly STRING
);
