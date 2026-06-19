docker run --name gtfsdb --memory="16g" --cpus="8.0" gtfsdb -d postgresql+psycopg2://ott:ott@ubuntudocker-vm/mbta -p --is_geospatial -b 5000 https://cdn.mbta.com/MBTA_GTFS.zip
