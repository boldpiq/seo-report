#!/bin/bash
# Boldpiq — Website Visibility Report
#   ./seo-report.sh https://clientdomain.co.za
#   ./seo-report.sh clientdomain.co.za --client "Client Name" --open
exec python3 "$(dirname "$0")/seo_report.py" "$@"
