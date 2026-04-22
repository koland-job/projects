# Monthly Sales Reporting — Python Automation

Automated monthly sales reporting pipeline built on the [Online Retail II](https://www.kaggle.com/datasets/lakshmi25npathi/online-retail-dataset) dataset.

The goal is not to explore or research the data, but to build a repeatable reporting workflow: calculate core KPIs, aggregate product and customer metrics, and export results to a structured Excel file ready for management use.

---

## Output

A formatted Excel report (`monthly_sales_report.xlsx`) with 12 sheets, each containing a data table and embedded charts.

| Sheet | Contents |
|---|---|
| `management_summary` | Core KPIs for the full reporting period |
| `monthly_trends` | Revenue, orders, AOV, unique customers by month |
| `daily_last_month` | Day-by-day revenue for the most recent complete month |
| `top_products_revenue` | Top 10 products by revenue |
| `top_products_units` | Top 10 products by units sold |
| `abc_products_summary` | ABC segment summary — products |
| `abc_products_full` | Full ABC classification for all products |
| `abc_customers_summary` | ABC segment summary — customers |
| `abc_customers_full` | Full ABC classification for all customers |
| `top_customers_revenue` | Top 10 customers by revenue |
| `customer_concentration` | Top 10 customers by revenue share |
| `repeat_vs_onetime` | Repeat vs one-time customer breakdown |

---

## Project Structure

```
├── data/
│   └── online_retail_II.xlsx        # source dataset (downloaded via Kaggle API)
├── output/
│   └── monthly_sales_report.xlsx    # generated report
└── project_2.ipynb                  # main notebook
```

---

## Notebook Structure

```
1.  Introduction
2.  Business Problem
3.  Dataset Description
4.  Data Preparation
    4.1  Data Cleaning
    4.2  Feature Engineering
5.  Report Logic / KPI Definitions
6.  Monthly Sales Performance
    6.1  Monthly Revenue
    6.2  Daily Revenue — Most Recent Complete Month
    6.3  Monthly Orders
    6.4  Monthly Average Order Value
    6.5  Monthly Unique Customers
7.  Product Performance Analysis
    7.1  Top Products by Revenue
    7.2  Top Products by Units Sold
8.  ABC Analysis
    8.1  ABC Analysis by Product
    8.2  ABC Analysis by Customer
9.  Customer Contribution Analysis
    9.1  Top Customers by Revenue
    9.2  Revenue Concentration by Customer
    9.3  Repeat vs One-Time Customers
10. Excel Export
```

---

## KPI Definitions

| KPI | Definition |
|---|---|
| Revenue | `quantity × price`, excluding cancellations and non-product rows |
| Orders | Count of unique invoice numbers per period |
| AOV | Total revenue / total orders |
| Unique Customers | Count of distinct `customer_id` values (identified customers only) |
| ABC Segment A | Products / customers contributing the first 80% of total revenue |
| ABC Segment B | Next 15% (cumulative 80–95%) |
| ABC Segment C | Remaining long tail |
| Repeat Customer | Customer with more than one unique invoice |
| One-Time Customer | Customer with exactly one invoice |

---

## Data Cleaning

- Removed duplicate rows
- Excluded rows with negative quantity or price (returns, corrections)
- Excluded cancelled invoices (invoice numbers containing letters)
- Excluded non-product stock codes (service rows, postage, etc.)
- Converted `customer_id` from float to nullable integer
- All monthly charts and KPI tables are cut at the last **complete** month to avoid mixing full and partial reporting periods

---

## Stack

- Python 3
- pandas
- numpy
- matplotlib
- seaborn
- openpyxl

---

## Dataset

**Online Retail II** — UCI Machine Learning Repository / Kaggle  
UK-based non-store online retailer, transaction data from December 2009 to December 2011.  
The company primarily sells gift-ware; a significant portion of customers are wholesalers.

Source: https://www.kaggle.com/datasets/lakshmi25npathi/online-retail-dataset

To run the notebook, place `online_retail_II.xlsx` in the `data/` folder, or configure Kaggle API credentials — the notebook will download the file automatically if it is not found.

---

## How to Run

```bash
pip install pandas numpy matplotlib seaborn openpyxl kaggle

jupyter notebook project_2.ipynb
```

Run all cells top to bottom (`Kernel → Restart & Run All`).  
The Excel report will be saved to `output/monthly_sales_report.xlsx`.
