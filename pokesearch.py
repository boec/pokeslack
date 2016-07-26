import json
import logging
import math
import random
import time

from datetime import datetime
from pgoapi.utilities import f2i
from s2sphere import CellId, LatLng

from pokedata import Pokedata, parse_map

logger = logging.getLogger(__name__)

REQ_SLEEP = 1

#Constants for Hex Grid
#Gap between vertical and horzonal "rows"
lat_gap_meters = 150
lng_gap_meters = 86.6

#111111m is approx 1 degree Lat, which is close enough for this
meters_per_degree = 111111
lat_gap_degrees = float(lat_gap_meters) / meters_per_degree

def calculate_lng_degrees(lat):
    return float(lng_gap_meters) / (meters_per_degree * math.cos(math.radians(lat)))

class Pokesearch:
    def __init__(self, api, auth_service, username, password, position):
        self.api = api
        self.auth_service = auth_service
        self.username = username
        self.password = password
        self.position = position

    def login(self):
        logger.info('login start with service: %s', self.auth_service)

        self.api.set_position(*self.position)

        while not self.api.login(self.auth_service, self.username, self.password):
            logger.warn('failed to login to pokemon go, retrying...')
            time.sleep(REQ_SLEEP)

        logger.info('login successful')

    def search(self, position, step_limit, step_size):
        if self.api._auth_provider and self.api._auth_provider._ticket_expire:
            if isinstance(self.api._auth_provider._ticket_expire, (int, long)):
                remaining_time = self.api._auth_provider._ticket_expire / 1000.0 - time.time()
                if remaining_time > 60:
                    logger.info("Skipping Pokemon Go login process since already logged in for another {:.2f} seconds".format(remaining_time))
                else:
                    self.login()
            else:
                logger.warn("skipping login since _ticket_expire was a token.")
        else:
            self.login()

        all_pokemon = {}
        i = 1
        for coord in generate_location_steps2(position, step_size, step_limit):
            lat = coord[0]
            lng = coord[1]
            self.api.set_position(*coord)

            cell_ids = get_cell_ids(lat, lng)
            timestamps = [0,] * len(cell_ids)

            self.api.get_map_objects(latitude = f2i(lat), longitude = f2i(lng), since_timestamp_ms = timestamps, cell_id = cell_ids)
            response_dict = self.api.call()

            while not response_dict:
                logger.info('Map Download failed. Trying again.')
                self.api.get_map_objects(latitude = f2i(lat), longitude = f2i(lng), since_timestamp_ms = timestamps, cell_id = cell_ids)
                response_dict = self.api.call()
                time.sleep(REQ_SLEEP)

            try:
                pokemons, pokestops, gyms = parse_map(response_dict)
            except KeyError as e:
                logger.error('failed to parse map with key error: %s', e)

            for key in pokemons.keys():
                if not key in all_pokemon:
                    pokemon = pokemons[key]
                    pokemon_id = pokemon['pokemon_id']
                    pokedata = Pokedata.get(pokemon_id)
                    pokemon['name'] = pokedata['name']
                    pokemon['rarity'] = pokedata['rarity']
                    pokemon['key'] = key
                    all_pokemon[key] = pokemon
                    yield pokemon
                # else:
                #     logger.info("have duplicate poke: %s", key)
            total_steps = (3 * (step_limit**2)) - (3 * step_limit) + 1
            logger.info('Completed {:5.2f}% of scan.'.format(float(i) / total_steps * 100))
            i += 1
            time.sleep(REQ_SLEEP)

def generate_location_steps2(position, step_size, num_steps):

    ring = 1 #Which ring are we on, 0 = center
    lat_location = position[0]
    lng_location = position[1]

    yield (lat_location, lng_location, 0) #Middle circle

    while ring < num_steps:
        #Move the location diagonally to top left spot, then start the circle which will end up back here for the next ring
        #Move Lat north first
        lat_location += lat_gap_degrees
        lng_location -= calculate_lng_degrees(lat_location)

        for direction in range(6):
            for i in range(ring):
                if direction == 0: #Right
                    lng_location += calculate_lng_degrees(lat_location) * 2

                if direction == 1: #Right Down
                    lat_location -= lat_gap_degrees
                    lng_location += calculate_lng_degrees(lat_location)

                if direction == 2: #Left Down
                    lat_location -= lat_gap_degrees
                    lng_location -= calculate_lng_degrees(lat_location)

                if direction == 3: #Left
                    lng_location -= calculate_lng_degrees(lat_location) * 2

                if direction == 4: #Left Up
                    lat_location += lat_gap_degrees
                    lng_location -= calculate_lng_degrees(lat_location)

                if direction == 5: #Right Up
                    lat_location += lat_gap_degrees
                    lng_location += calculate_lng_degrees(lat_location)

                yield (lat_location, lng_location, 0) #Middle circle

        ring += 1

def generate_location_steps(position, step_size, step_limit):
    pos, x, y, dx, dy = 1, 0, 0, 0, -1
    while -step_limit / 2 < x <= step_limit / 2 and -step_limit / 2 < y <= step_limit / 2:
        yield (x * step_size + position[0], y * step_size + position[1], 0)
        if x == y or (x < 0 and x == -y) or (x > 0 and x == 1 - y):
            dx, dy = -dy, dx
        x, y = x + dx, y + dy

def generate_spiral(position, step_size, step_limit):
    yield (position[0], position[1], 0)
    steps,x,y,d,m = 1, 0, 0, 1, 1
    rlow = 0.0
    rhigh = 0.0005

    while steps < step_limit:
        while 2 * x * d < m and steps < step_limit:
            x = x + d
            steps += 1
            lat = x * step_size + position[0] + random.uniform(rlow, rhigh)
            lng = y * step_size + position[1] + random.uniform(rlow, rhigh)
            yield (lat, lng, 0)
        while 2 * y * d < m and steps < step_limit:
            y = y + d
            steps += 1
            lat = x * step_size + position[0] + random.uniform(rlow, rhigh)
            lng = y * step_size + position[1] + random.uniform(rlow, rhigh)
            yield (lat, lng, 0)

        d = -1 * d
        m = m + 1

def get_cell_ids(lat, lng, radius = 10):
    origin = CellId.from_lat_lng(LatLng.from_degrees(lat, lng)).parent(15)
    walk = [origin.id()]
    right = origin.next()
    left = origin.prev()

    # Search around provided radius
    for i in range(radius):
        walk.append(right.id())
        walk.append(left.id())
        right = right.next()
        left = left.prev()

    # Return everything
    return sorted(walk)
