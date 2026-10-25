"""Patrol: an uptime monitor built to run on Kubernetes (EKS).

Three services share this package:
  scheduler    - posts one patrol order per site to SQS, then exits (a CronJob)
  worker       - takes orders, checks the site, saves the result to DynamoDB
  status_page  - web page + JSON API + Prometheus metrics, read from DynamoDB
"""

__version__ = "0.1.0"
