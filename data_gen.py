"""
Generates a synthetic health-surveillance dataset shaped like real
IDSP (disease surveillance) + HMIS (immunization) data, and loads it
into a local SQLite DB (health.db) that stands in for BigQuery.

Swap-to-real-data note:
  Once you download a real CSV from IDSP/Dataful/GHDx, just replace the
  generation step below with `pd.read_csv(...)` matching these same
  column names, and everything downstream (NL2SQL, anomaly detection,
  agent) keeps working unchanged.
"""

import os
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta

np.random.seed(42)

DB_PATH = "data/health.db"

DISTRICTS_BLOCKS_VILLAGES = {
    "Kanpur Dehat": {
        "Bhognipur": ["Rampur", "Sikandra", "Jaswantnagar"],
        "Rasoolabad": ["Ghatampur", "Neemsar", "Devipur"],
    },
    "Unnao": {
        "Bangarmau": ["Kasimpur", "Bighapur", "Sohramau"],
        "Safipur": ["Tiwaripur", "Ajgain", "Nawabganj"],
    },
    "Rae Bareli": {
        "Dalmau": ["Salon", "Maharajganj", "Bachhrawan"],
        "Unchahar": ["Jais", "Harchandpur", "Rohaniya"],
    },
}

DISEASES = ["Dengue", "Malaria", "Acute Diarrhoeal Disease", "Chikungunya", "Typhoid", "Seasonal Flu"]

VACCINES = ["BCG", "OPV", "Pentavalent", "Measles-Rubella"]

N_WEEKS = 26  # ~6 months of weekly data
START_DATE = datetime(2026, 1, 5)


def gen_symptom_reports():
    rows = []
    for week_i in range(N_WEEKS):
        week_date = START_DATE + timedelta(weeks=week_i)
        for district, blocks in DISTRICTS_BLOCKS_VILLAGES.items():
            for block, villages in blocks.items():
                for village in villages:
                    population = np.random.randint(1500, 6000)
                    for disease in DISEASES:
                        base_rate = {
                            "Dengue": 2, "Malaria": 3, "Acute Diarrhoeal Disease": 4,
                            "Chikungunya": 1, "Typhoid": 2, "Seasonal Flu": 5,
                        }[disease]
                        cases = max(0, int(np.random.poisson(base_rate)))

                        # --- Inject a deliberate anomaly: Dengue outbreak in
                        # Sikandra village (Bhognipur block) in weeks 15-17 ---
                        if (disease == "Dengue" and village == "Sikandra"
                                and 15 <= week_i <= 17):
                            cases += np.random.randint(18, 30)

                        # --- Inject a slow-building Malaria cluster in Jais
                        # village weeks 20 onward (gradual anomaly) ---
                        if disease == "Malaria" and village == "Jais" and week_i >= 20:
                            cases += (week_i - 19) * np.random.randint(3, 6)

                        rows.append({
                            "report_date": week_date.strftime("%Y-%m-%d"),
                            "district": district,
                            "block": block,
                            "village": village,
                            "disease": disease,
                            "cases": cases,
                            "population": population,
                        })
    return pd.DataFrame(rows)


def gen_immunization():
    rows = []
    for week_i in range(N_WEEKS):
        week_date = START_DATE + timedelta(weeks=week_i)
        for district, blocks in DISTRICTS_BLOCKS_VILLAGES.items():
            for block, villages in blocks.items():
                for village in villages:
                    for vaccine in VACCINES:
                        children_due = np.random.randint(20, 60)
                        coverage_rate = np.random.uniform(0.80, 0.97)

                        # --- Inject an immunization drop-off (vaccine
                        # stock-out) in Neemsar village, weeks 10-12 ---
                        if village == "Neemsar" and 10 <= week_i <= 12:
                            coverage_rate = np.random.uniform(0.35, 0.55)

                        children_covered = int(children_due * coverage_rate)
                        rows.append({
                            "report_date": week_date.strftime("%Y-%m-%d"),
                            "district": district,
                            "block": block,
                            "village": village,
                            "vaccine": vaccine,
                            "children_due": children_due,
                            "children_covered": children_covered,
                        })
    return pd.DataFrame(rows)


def main():
    import sys
    symptom_df = gen_symptom_reports()
    immun_df = gen_immunization()

    if "--to-csv" in sys.argv:
        os.makedirs("data", exist_ok=True)
        symptom_df.to_csv("data/symptom_reports.csv", index=False)
        immun_df.to_csv("data/immunization_records.csv", index=False)
        print("Wrote data/symptom_reports.csv and data/immunization_records.csv")
        print("Now load them into BigQuery with:")
        print("  bq load --source_format=CSV --skip_leading_rows=1 "
              "YOUR_PROJECT:swasthya_ai.symptom_reports data/symptom_reports.csv "
              "report_date:DATE,district:STRING,block:STRING,village:STRING,disease:STRING,cases:INTEGER,population:INTEGER")
        print("  bq load --source_format=CSV --skip_leading_rows=1 "
              "YOUR_PROJECT:swasthya_ai.immunization_records data/immunization_records.csv "
              "report_date:DATE,district:STRING,block:STRING,village:STRING,vaccine:STRING,children_due:INTEGER,children_covered:INTEGER")
        return

    conn = sqlite3.connect(DB_PATH)
    symptom_df.to_sql("symptom_reports", conn, if_exists="replace", index=False)
    immun_df.to_sql("immunization_records", conn, if_exists="replace", index=False)
    conn.close()

    print(f"symptom_reports: {len(symptom_df)} rows")
    print(f"immunization_records: {len(immun_df)} rows")
    print(f"Saved to {DB_PATH}")


if __name__ == "__main__":
    main()
