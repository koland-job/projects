# Data Analytics Portfolio

A small set of end-to-end portfolio projects built on real retail data. Each project lives in its own folder with its own notebook, README, and outputs.

---

## Projects

### [Project 1 — Online Retail Analysis](./project-1-online-retail-analysis)

Exploratory analysis of UK online retail data: revenue trends, customer behavior, retention, ABC segmentation. Demonstrates data cleaning, EDA, and translating findings into business recommendations.

**Stack:** Python, pandas, NumPy, seaborn, matplotlib, Jupyter

---

### [Project 2 — Automated Sales Report](./project-2-automated-sales-report)

Repeatable monthly sales reporting pipeline. Takes raw transactional data and produces a single formatted Excel file with 12 sheets — KPIs, monthly trends, top products, ABC, and customer concentration. Designed for hand-off to management without manual editing.

**Stack:** Python, pandas, NumPy, openpyxl, matplotlib, Jupyter

---

### [Project 3 — Tableau Sales Dashboard](./project-3-tableau-sales-dashboard)

Interactive Tableau dashboard backed by a Python data-prep pipeline. Headline KPIs with period-over-period comparison, revenue and orders trends, top products, ABC segmentation, and customer mix — all driven by a single date-range parameter.

**Stack:** Python (data prep) + Tableau (visualization)

---

## Dataset

All three projects use the [Online Retail II](https://www.kaggle.com/datasets/lakshmi25npathi/online-retail-dataset) dataset — a UK-based non-store online retailer's transactional data from December 2009 to December 2011.

The raw `.xlsx` file is excluded from the repository (see each project's `.gitignore`). To run the notebooks, place `online_retail_II.xlsx` in the project's `data/` folder, or configure Kaggle API credentials and the notebooks will download it automatically.

---

## How to Run

Each project is self-contained. Open its folder, install the listed dependencies, and run the notebook top to bottom:

```bash
cd project-1-online-retail-analysis  # or project-2 / project-3
pip install -r requirements.txt       # if requirements.txt is provided, otherwise see project README
jupyter notebook
```

The Tableau workbook in project 3 is opened separately in Tableau Desktop / Tableau Public.
