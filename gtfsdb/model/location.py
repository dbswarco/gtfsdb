import json

from sqlalchemy import Column, String, Integer
from sqlalchemy.orm import deferred
from sqlalchemy.sql import func

from gtfsdb import config, util
from gtfsdb.model.base import Base

import logging
log = logging.getLogger(__name__)

RSO_MAX=11111


class LocationBase(object):
    route_id = Column(String(512))
    route_sort_order = Column(Integer, index=True)
    render_order = Column(Integer, default=1)
    region_name = Column(String(512))
    region_color = Column(String(7), default=config.default_route_color)
    text_color = Column(String(7), default=config.default_text_color)

    @classmethod
    def add_geometry_column(cls):
        if not hasattr(cls, 'geom'):
            from geoalchemy2 import Geometry
            # TODO: the geom could be either a Polygon or Multi-Polygon 
            cls.geom = deferred(Column(Geometry('POLYGON', srid=config.SRID)))


class Location(Base, LocationBase):
    """ GTFS 'location' aka Flex regions """
    datasource = config.DATASOURCE_GTFS
    filename = 'locations.geojson'

    __tablename__ = 'locations'

    id = Column(String(512), primary_key=True, index=True, nullable=False)

    @classmethod
    def make_record(cls, row, **kwargs):
        if row.get('geometry') and hasattr(cls, 'geom'):
            #import pdb; pdb.set_trace()
            row['geom'] = json.dumps(row['geometry'])

            # the id attribute is often in the location_id; but for C-TRAN's pretty shapes, that id is in properties
            if row.get('properties') and row.get('properties').get('location_id'):
                row['id'] = row.get('properties').get('location_id')

        return row

    @classmethod
    def post_process(cls, db, **kwargs):
        """
        fix up and populate the location table:
         - find each location's route_id and other details via stop_times table
         - create a simplified geom for rendering
         - ...
        """
        from gtfsdb.model.stop_time import StopTime
        from sqlalchemy.orm import joinedload

        batch_size = kwargs.get('batch_size', config.DEFAULT_BATCH_SIZE)
        log.info("{0}.post_process: starting with batch size {1}".format(cls.__name__, batch_size))
        session = db.session
        try:
            rso = {}
            total_locs = session.query(func.count(Location.id)).scalar()
            offset = 0

            while offset < total_locs:
                locs = (session.query(Location)
                       .order_by(Location.id)
                       .limit(batch_size)
                       .offset(offset)
                       .all())

                if not locs:
                    break

                count = 0
                for i, l in enumerate(locs):
                    stop_time = (session.query(StopTime)
                                .options(joinedload(StopTime.trip).joinedload('route'))
                                .filter_by(location_id=l.id)
                                .first())
                    if stop_time:
                        #import pdb; pdb.set_trace()
                        # Cache route data before potential detachment
                        route_id = stop_time.trip.route.route_id
                        route_sort_order = stop_time.trip.route.route_sort_order
                        route_name = stop_time.trip.route.route_name
                        route_color = stop_time.trip.route.route_color
                        route_text_color = stop_time.trip.route.route_text_color

                        rso[route_id] = rso.get(route_id) or (offset + i)  # for the 'default' route sort order below
                        l.route_id = route_id
                        l.route_sort_order = route_sort_order or rso.get(route_id) or 1
                        l.render_order = RSO_MAX - l.route_sort_order
                        l.region_name = route_name
                        l.region_color = route_color
                        l.text_color = route_text_color
                        session.merge(l)
                        count += 1

                # Commit this batch
                session.commit()
                session.flush()
                session.expunge_all()
                offset += batch_size

            # Final commit
            session.commit()
            session.flush()
        except Exception as e:
            log.error(e)
        finally:
            session.commit()
            session.flush()


class FlexRegion(Base, LocationBase):
    """ a union of GTFS related (via stop_time.location_id) flex regions, that should look cartographically good """
    datasource = config.DATASOURCE_DERIVED
    __tablename__ = 'flex_region'

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    @classmethod
    def post_process(cls, db, **kwargs):
        """
        query the location table, which unions all regions for the agency (group) by the route and other ids
        example sql:
            --drop table if exists sam.flex_region;
            --UPDATE TABLE sam.flex_region
            insert into sam.flex_region (route_id, route_sort_order, render_order, region_name, region_color, text_color, geom)
            SELECT route_id, region_name, region_color, text_color, 
                    ST_UnaryUnion(ST_CollectionExtract(unnest(ST_ClusterIntersecting(geom)))) as geom
            FROM sam.locations
            group by route_id, region_name, region_color, text_color;
        """
        #import pdb; pdb.set_trace()
        route_columns = "route_id, route_sort_order, render_order, region_name, region_color, text_color"
        schema = kwargs.get('schema', 'public')
        sql = "" \
        "INSERT INTO {schema}.flex_region ({route_columns}, geom) " \
        "SELECT {route_columns}, ST_UnaryUnion(ST_CollectionExtract(unnest(ST_ClusterIntersecting(geom)))) as geom " \
        "FROM {schema}.locations " \
        "GROUP BY {route_columns}".format(schema=schema, route_columns=route_columns)
        util.do_sql(db, sql)
