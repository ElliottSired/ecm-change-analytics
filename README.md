# Engineering Change Management Analytics

A dimensional model and analytical pipeline for engineering change management on a nuclear EPC programme, surfacing programme risk invisible to standard change register reporting.

Standard change management systems report how many changes are open and against which milestone they were recorded. This pipeline derives the driving milestone for each change — the earliest milestone deadline across all impacted systems — using a SQL window function partitioned by change and ranked by target date. On a synthetic register of 15,000 changes, 74.5% drive an earlier milestone than recorded at raise. The gap represents concentration of programme risk against near-term deadlines the programme does not recognise.

The pipeline implements a Kimball star schema with bridge tables resolving many-to-many relationships between changes and systems, and between systems and physical buildings. Analysis spans four grains — individual change, system, contract, and building/zone — covering backlog, stagnation scoring, velocity-versus-backlog quadrants, lead time distributions, and readiness widgets. A multi-condition risk flag combines deadline proximity with stagnation score to produce an actionable intervention list at change grain.

## To run

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/ElliottSired/ecm-change-analytics/blob/main/EPC_Change_Management_Analysis.ipynb)

    git clone https://github.com/ElliottSired/ecm-change-analytics.git
    cd ecm-change-analytics
    pip install -r requirements.txt
    jupyter notebook

## Data

The data is synthetic, generated inside the notebook to exhibit the patterns the pipeline is designed to detect. The structural approach and analytical techniques are directly applicable to real production data.

## Further reading

For a detailed architectural walkthrough including cell-by-cell logic, known issues, and future improvements, see [ARCHITECTURE.md](./ARCHITECTURE.md).