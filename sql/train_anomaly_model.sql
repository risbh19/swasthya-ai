

CREATE OR REPLACE MODEL `swasthya_ai.disease_spike_model`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'report_date',
  time_series_data_col = 'cases',
  time_series_id_col = 'series_id',
  auto_arima = TRUE,
  data_frequency = 'WEEKLY',
  decompose_time_series = TRUE
) AS
SELECT
  report_date,
  CONCAT(village, '::', disease) AS series_id,
  cases
FROM `swasthya_ai.symptom_reports`;

CREATE OR REPLACE MODEL `swasthya_ai.immunization_dropoff_model`
OPTIONS(
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'report_date',
  time_series_data_col = 'coverage_rate',
  time_series_id_col = 'series_id',
  auto_arima = TRUE,
  data_frequency = 'WEEKLY',
  decompose_time_series = TRUE
) AS
SELECT
  report_date,
  CONCAT(village, '::', vaccine) AS series_id,
  SAFE_DIVIDE(children_covered, children_due) AS coverage_rate
FROM `swasthya_ai.immunization_records`;


