# Online Retail Analysis Portfolio Project

## Overview
This portfolio project analyzes the Online Retail II dataset to explore sales performance, customer behavior, and retention patterns in a UK-based online retail business. The analysis is designed to answer practical business questions and show how transactional data can be translated into actionable recommendations.

## Business Questions
- What is the total revenue?
- How does revenue change over time?
- What are the top-selling products?
- What is the average order value?
- How many customers are new vs returning?
- What does customer retention look like over time?
- What business recommendations can be made based on the analysis?

## Key Metrics
- 1,033,036 transaction rows after removing duplicates
- 53,628 unique invoices
- 5,942 unique customers in the full dataset
- 5,853 identified customers in customer-level analysis
- £19.66M total revenue in the cleaned sales dataset
- 43 countries in the full dataset
- Top 5% of customers generate about 52% of total revenue

## Dataset
- Source: Online Retail II dataset from Kaggle
- Period covered: December 2009 to December 2011
- File used: `online_retail_II.xlsx`

### Notes About the Data
- The dataset contains two worksheets, both of which were combined into one DataFrame.
- The raw data includes cancellations, manual adjustments, non-product stock codes, and missing customer IDs.
- A separate product category column is not available, so category-level analysis was not included.

## Tools Used
- Python
- Pandas
- NumPy
- Seaborn
- Matplotlib
- Jupyter Notebook

## Project Structure
- Data extraction
- Data cleaning
- Exploratory data analysis
- Sales analysis
- Customer analysis
- Cohort / retention analysis
- Business insights
- Recommendations
- Conclusion

## Data Preparation
The data was prepared in two stages:
- Initial cleaning: standardized column names, removed duplicates, created `invoice_month` and `revenue`
- Analytical subsets:
  - `df_sales` for sales metrics
  - `df_customers` for customer and retention analysis

## Key Findings
- The business is highly concentrated in the UK market.
- Revenue and transaction activity show clear seasonality, with strong peaks in November.
- Customer value is highly concentrated: the top 5% of customers generate about 52% of total revenue.
- Returning customers play an important role in long-term business performance.
- Large invoice sizes and high quantities suggest a strong wholesale component in the customer base.

## Recommendations
- Prioritize retention of high-value customers.
- Improve second-purchase conversion.
- Prepare inventory and operations early for seasonal peaks.
- Consider separate strategies for wholesale-like and regular customers.
- Improve customer identification and transaction labeling for better analysis quality.

## Why This Project Matters
This project shows how a raw transactional dataset can be transformed into business insight. It combines data cleaning, exploratory analysis, customer analytics, and retention analysis to support decisions related to revenue growth, customer retention, and seasonal planning.

## Limitations
- The dataset does not contain a reliable product category field.
- Some transactions do not include customer IDs, which limits customer-level analysis.
- The final month in the dataset is incomplete and should be interpreted carefully in time-based charts.

## Files
- `Project № 1.ipynb` — main notebook with the full analysis
- `data/online_retail_II.xlsx` — source dataset

## How to Use
1. Open the notebook `Project № 1.ipynb`.
2. Run the cells in order.
3. Review the analysis, charts, insights, and recommendations.

## Project Goal
This notebook was created as a portfolio project for freelance data analytics work.
