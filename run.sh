#!/bin/bash
python -c "from micronote import config; config.create_indexes()"
exec gunicorn -t 300 -w 2 --threads 4 -b 0.0.0.0:5005 --log-level info "micronote.app:app"
