#!/usr/bin/python

#================================================================================#
#                             ADS-B FEEDER PORTAL                                #
# ------------------------------------------------------------------------------ #
# Copyright and Licensing Information:                                           #
#                                                                                #
# The MIT License (MIT)                                                          #
#                                                                                #
# Copyright (c) 2015-2016 Joseph A. Prochazka                                    #
#                                                                                #
# Permission is hereby granted, free of charge, to any person obtaining a copy   #
# of this software and associated documentation files (the "Software"), to deal  #
# in the Software without restriction, including without limitation the rights   #
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell      #
# copies of the Software, and to permit persons to whom the Software is          #
# furnished to do so, subject to the following conditions:                       #
#                                                                                #
# The above copyright notice and this permission notice shall be included in all #
# copies or substantial portions of the Software.                                #
#                                                                                #
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR     #
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,       #
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE    #
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER         #
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,  #
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE  #
# SOFTWARE.                                                                      #
#================================================================================#

# WHAT THIS DOES:
# ---------------------------------------------------------------
#
# 1) Read aircraft.json generated by dump1090-mutability.
# 2) Add the flight to the database if it does not already exist.
# 3) Update the last time the flight was seen.

import datetime
import inotify.adapters
import json
import re
import time
import os
#import urllib2

def log(string):
    #print(string) # uncomment to enable debug logging
    return

# Read the configuration file.
with open(os.path.dirname(os.path.realpath(__file__)) + '/config.json') as config_file:
    config = json.load(config_file)

# Import the needed database library.
if config["database"]["type"] == "mysql":
    import MySQLdb
else:
    import sqlite3

class FlightsProcessor(object):
    def __init__(self, config):
        self.config = config
        self.dbType = config["database"]["type"]
        # List of required keys for position data entries
        self.position_keys = ('lat', 'lon', 'altitude', 'speed', 'track', 'vert_rate')

    def setupDBStatements(self, formatSymbol):
        if hasattr(self, 'STMTS'):
            return
        mapping = { "s": formatSymbol }
        self.STMTS = {
            'select_aircraft_count':"SELECT COUNT(*) FROM adsb_aircraft WHERE icao = %(s)s" % mapping,
            'select_aircraft_id':   "SELECT id FROM adsb_aircraft WHERE icao = %(s)s" % mapping,
            'select_flight_count':  "SELECT COUNT(*) FROM adsb_flights WHERE flight = %(s)s" % mapping,
            'select_flight_id':     "SELECT id FROM adsb_flights WHERE flight = %(s)s" % mapping,
            'select_position':      "SELECT message FROM adsb_positions WHERE flight = %(s)s AND message = %(s)s ORDER BY time DESC LIMIT 1" % mapping,
            'insert_aircraft':      "INSERT INTO adsb_aircraft (icao, firstSeen, lastSeen) VALUES (%(s)s, %(s)s, %(s)s)" % mapping,
            'insert_flight':        "INSERT INTO adsb_flights (aircraft, flight, firstSeen, lastSeen) VALUES (%(s)s, %(s)s, %(s)s, %(s)s)" % mapping,
            'insert_position_sqwk': "INSERT INTO adsb_positions (flight, time, message, squawk, latitude, longitude, track, altitude, verticleRate, speed) VALUES (%(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s)" % mapping,
            'insert_position':      "INSERT INTO adsb_positions (flight, time, message, latitude, longitude, track, altitude, verticleRate, speed) VALUES (%(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s, %(s)s)" % mapping,
            'update_aircraft_seen': "UPDATE adsb_aircraft SET lastSeen = %(s)s WHERE icao = %(s)s" % mapping,
            'update_flight_seen':   "UPDATE adsb_flights SET aircraft = %(s)s, lastSeen = %(s)s WHERE flight = %(s)s" % mapping
        }

    def connectDB(self):
        if self.dbType == "sqlite": ## Connect to a SQLite database.
            self.setupDBStatements("?")
            return sqlite3.connect(self.config["database"]["db"])
        else: ## Connect to a MySQL database.
            self.setupDBStatements("%s")
            return MySQLdb.connect(host=self.config["database"]["host"],
                user=self.config["database"]["user"],
                passwd=self.config["database"]["passwd"],
                db=self.config["database"]["db"])

    def processAircraftList(self, aircraftList):
        db = self.connectDB()
        # Get Database cursor handle
        self.cursor = db.cursor()
        # Assign the time to a variable.
        self.time_now = datetime.datetime.utcnow().strftime("%Y/%m/%d %H:%M:%S")

        for aircraft in aircraftList:
            self.processAircraft(aircraft)

        # Close the database connection.
        db.commit()
        db.close()

    def processAircraft(self, aircraft):
        hexcode = aircraft["hex"]
        # Check if this aircraft was already seen.
        self.cursor.execute(self.STMTS['select_aircraft_count'], (hexcode,))
        row_count = self.cursor.fetchone()
        if row_count[0] == 0:
            # Insert the new aircraft.
            log("Added Aircraft: " + hexcode)
            self.cursor.execute(self.STMTS['insert_aircraft'], (hexcode, self.time_now, self.time_now,))
        else:
            # Update the existing aircraft.
            self.cursor.execute(self.STMTS['update_aircraft_seen'], (self.time_now, hexcode,))
            log("Updating Aircraft: " + hexcode)
        # Get the ID of this aircraft.
        self.cursor.execute(self.STMTS['select_aircraft_id'], (hexcode,))
        row = self.cursor.fetchone()
        aircraft_id = row[0]
        log("\tFound Aircraft ID: " + str(aircraft_id))

        # Check that a flight is tied to this track.
        if aircraft.has_key('flight'):
            self.processFlight(aircraft_id, aircraft)

    def processFlight(self, aircraft_id, aircraft):
        flight = aircraft["flight"].strip()
        # Check to see if the flight already exists in the database.
        self.cursor.execute(self.STMTS['select_flight_count'], (flight,))
        row_count = self.cursor.fetchone()
        if row_count[0] == 0:
            # If the flight does not exist in the database add it.
            params = (aircraft_id, flight, self.time_now, self.time_now,)
            self.cursor.execute(self.STMTS['insert_flight'], params)
            log("\t\tAdded Flight: " + flight)
        else:
            # If it already exists pdate the time it was last seen.
            params = (aircraft_id, self.time_now, flight,)
            self.cursor.execute(self.STMTS['update_flight_seen'], params)
            log("\t\tUpdated Flight: " + flight)
        # Get the ID of this flight.
        self.cursor.execute(self.STMTS['select_flight_id'], (flight,))
        row = self.cursor.fetchone()
        flight_id = row[0]

        # Check if position data is available.
        if (all (k in aircraft for k in self.position_keys) and aircraft["altitude"] != "ground"):
            self.processPositions(flight_id, aircraft)

    def processPositions(self, flight_id, aircraft):
        # Check that this message has not already been added to the database.
        params = (flight_id, aircraft["messages"],)
        self.cursor.execute(self.STMTS['select_position'], params)
        row = self.cursor.fetchone()

        if row == None or row[0] != aircraft["messages"]:
            # Add this position to the database.
            if aircraft.has_key('squawk'):
                params = (flight_id, self.time_now, aircraft["messages"], aircraft["squawk"],
                            aircraft["lat"], aircraft["lon"], aircraft["track"],
                            aircraft["altitude"], aircraft["vert_rate"], aircraft["speed"],)
                self.cursor.execute(self.STMTS['insert_position_sqwk'], params)
                log("\t\t\tInserted position w/ Squawk " + repr(params))
            else:
                params = (flight_id, self.time_now, aircraft["messages"], aircraft["lat"], aircraft["lon"],
                            aircraft["track"], aircraft["altitude"], aircraft["vert_rate"], aircraft["speed"],)
                self.cursor.execute(self.STMTS['insert_position'], params)
                log("\t\t\tInserted position w/o Squawk " + repr(params))
        else:
            log("\t\t\tMessage is the same")


if __name__ == "__main__":
    processor = FlightsProcessor(config)

    mutability_dir = '/run/dump1090-mutability/'
    i = inotify.adapters.Inotify()
    i.add_watch(mutability_dir)

    # Main run loop
    for event in i.event_gen():
        if event is not None:
            (header, type_names, watch_path, filename) = event
            if 'IN_MOVED_TO' in type_names and re.match('^history_\d+\.json$', filename):

                # Read dump1090-mutability's aircraft.json.
                #with open('/run/dump1090-mutability/aircraft.json') as data_file:
                with open(mutability_dir + filename) as data_file:
                    data = json.load(data_file)
                # For testing using a remote JSON feed.
                #response = urllib2.urlopen('http://192.168.254.2/dump1090/data/aircraft.json')
                #data = json.load(response)

                processor.processAircraftList(data["aircraft"])

                log("Last Run: " + datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S"))

