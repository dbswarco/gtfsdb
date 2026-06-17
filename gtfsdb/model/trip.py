import logging

from gtfsdb import config
from gtfsdb.model.base import Base
from sqlalchemy import Column, func
from sqlalchemy.orm import relationship
from sqlalchemy.types import Integer, String

log = logging.getLogger(__name__)


class Trip(Base):
    datasource = config.DATASOURCE_GTFS
    filename = 'trips.txt'

    __tablename__ = 'trips'

    trip_id = Column(String(512), primary_key=True, index=True, nullable=False)
    route_id = Column(String(512), index=True, nullable=False)
    service_id = Column(String(512), index=True, nullable=False)
    direction_id = Column(Integer, index=True)
    block_id = Column(String(512), index=True)
    shape_id = Column(String(512), index=True, nullable=True)
    trip_type = Column(String(256))

    trip_headsign = Column(String(512))
    trip_short_name = Column(String(512))
    bikes_allowed = Column(Integer, default=0)
    wheelchair_accessible = Column(Integer, default=0)

    pattern = relationship(
        'Pattern',
        primaryjoin='Trip.shape_id==Pattern.shape_id',
        foreign_keys='(Trip.shape_id)',
        uselist=False, viewonly=True)

    shapes = relationship(
        'Shape',
        primaryjoin='Trip.shape_id==Shape.shape_id',
        foreign_keys='(Trip.shape_id)',
        uselist=True, viewonly=True)

    route = relationship(
        'Route',
        primaryjoin='Trip.route_id==Route.route_id',
        foreign_keys='(Trip.route_id)',
        uselist=False, viewonly=True)

    stop_times = relationship(
        'StopTime',
        primaryjoin='Trip.trip_id==StopTime.trip_id',
        foreign_keys='(Trip.trip_id)',
        order_by='StopTime.stop_sequence',
        uselist=True, viewonly=True)

    universal_calendar = relationship(
        'UniversalCalendar',
        primaryjoin='Trip.service_id==UniversalCalendar.service_id',
        foreign_keys='(Trip.service_id)',
        uselist=True, viewonly=True)

    @classmethod
    def post_process(cls, db, **kwargs):
        from gtfsdb.model.stop_time import StopTime

        batch_size = kwargs.get('batch_size', config.DEFAULT_BATCH_SIZE)
        log.info("Trip.post_process: validating trips with batch size {}".format(batch_size))

        # Use SQL aggregation to find invalid trips (those with < 2 stop_times) efficiently
        # This avoids loading all trip and stop_time data into memory
        subquery = (
            db.session.query(
                StopTime.trip_id,
                func.count(StopTime.stop_sequence).label('stop_count')
            )
            .group_by(StopTime.trip_id)
            .having(func.count(StopTime.stop_sequence) < 2)
            .subquery()
        )

        # Get invalid trip IDs in batches
        invalid_trips_query = (
            db.session.query(Trip.trip_id)
            .join(subquery, Trip.trip_id == subquery.c.trip_id)
        )

        # Process in batches to avoid memory issues
        offset = 0
        while True:
            batch = invalid_trips_query.limit(batch_size).offset(offset).all()
            if not batch:
                break

            for trip_row in batch:
                # Get stop count for this specific trip
                stop_count = db.session.query(func.count(StopTime.stop_sequence)).filter(
                    StopTime.trip_id == trip_row.trip_id
                ).scalar()

                log.warning(
                    "invalid trip: {0} only has {1} stop_time record (i.e., maybe the stops are coded as "
                    "non-public, and thus their stop time records didn't make it into the gtfs)".format(
                        trip_row.trip_id, stop_count
                    )
                )

            offset += batch_size

            # Clear session to free memory
            db.session.expunge_all()

    @classmethod
    def query_trip(cls, session, trip_id, schema=None):
        """ return a trip via trip_id """
        ret_val = None
        try:
            if schema:
                Trip.set_schema(schema)
            ret_val = session.query(Trip).filter(Trip.trip_id==trip_id).one()
        except:
            log.debug(session.query(Trip).filter(Trip.trip_id==trip_id))

        return ret_val

    @property
    def start_stop(self):
        return self.stop_times[0].stop

    @property
    def end_stop(self):
        return self.stop_times[-1].stop

    @property
    def start_time(self):
        return self.stop_times[0].departure_time

    @property
    def end_time(self):
        return self.stop_times[-1].arrival_time

    @property
    def trip_len(self):
        ret_val = 0
        if self.stop_times:
            ret_val = len(self.stop_times)
        return ret_val

    @property
    def is_valid(self):
        # trip has to have multiple stop times to be valid, else it's not a trip...
        return self.trip_len >= 2
