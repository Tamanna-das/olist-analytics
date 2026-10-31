all: data/olist.db
	python src/run_sql.py
	python src/features.py
	python tests/test_leakage.py
	python src/train.py
	python src/backtest.py
	python src/export_powerbi.py

data/olist.db: sql/01_schema.sql
	python src/etl.py

# Full pipeline on synthetic Olist-shaped data (fake numbers; tests the code only)
smoke:
	python tests/make_synthetic_data.py --out data/synthetic --orders 20000
	python src/etl.py --raw data/synthetic --db data/smoke.db
	python src/run_sql.py --db data/smoke.db --out data/smoke_sql_outputs --rows 3
	python src/features.py --db data/smoke.db --out data/smoke_features.csv
	python tests/test_leakage.py --db data/smoke.db --features data/smoke_features.csv

.PHONY: all smoke
