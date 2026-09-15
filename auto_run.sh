#!/bin/bash

current_date=$(date +"%d/%m/%Y")
echo " current_date is: $current_date "

# Stops executing if any script throws an error
python3 scrape_marches.py  --start $current_date --end $current_date && \
python3 sqlite.py && \

echo "All scripts completed successfully!" || \
echo "A script failed; execution stopped."


