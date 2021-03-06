# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of Sick Beard.
#
# Sick Beard is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Sick Beard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Sick Beard.  If not, see <http://www.gnu.org/licenses/>.



import urllib2
import os.path
import sys
import datetime
import time

import xml.etree.cElementTree as etree

import sickbeard

from sickbeard import helpers, classes, exceptions, logger, db

from sickbeard.common import *
from sickbeard import tvcache
from sickbeard import encodingKludge as ek

from lib.tvnamer.utils import FileParser
from lib.tvnamer import tvnamer_exceptions

class GenericProvider:

    NZB = "nzb"
    TORRENT = "torrent"

    def __init__(self, name):

        # these need to be set in the subclass
        self.providerType = None
        self.name = name
        self.url = ''

        self.supportsBacklog = False

        self.cache = tvcache.TVCache(self)

    def getID(self):
        return GenericProvider.makeID(self.name)

    @staticmethod
    def makeID(name):
        return re.sub("[^\w\d_]", "_", name).lower()

    def imageName(self):
        return self.getID() + '.gif'

    def _checkAuth(self):
        return

    def isActive(self):
        if self.providerType == GenericProvider.NZB:
            return self.isEnabled() and sickbeard.USE_NZB
        elif self.providerType == GenericProvider.TORRENT:
            return self.isEnabled() and sickbeard.USE_TORRENT
        else:
            return False

    def isEnabled(self):
        """
        This should be overridden and should return the config setting eg. sickbeard.MYPROVIDER
        """
        return False

    def getResult(self, episodes):
        """
        Returns a result of the correct type for this provider
        """

        if self.providerType == GenericProvider.NZB:
            result = classes.NZBSearchResult(episodes)
        elif self.providerType == GenericProvider.TORRENT:
            result = classes.TorrentSearchResult(episodes)
        else:
            result = classes.SearchResult(episodes)

        result.provider = self

        return result


    def getURL(self, url, headers=None):
        """
        By default this is just a simple urlopen call but this method should be overridden
        for providers with special URL requirements (like cookies)
        """

        if not headers:
            headers = []

        result = None

        try:
            result = helpers.getURL(url, headers)
        except (urllib2.HTTPError, IOError), e:
            logger.log(u"Error loading "+self.name+" URL: " + str(sys.exc_info()) + " - " + str(e), logger.ERROR)
            return None

        return result

    def downloadResult (self, result):

        logger.log(u"Downloading a result from " + self.name+" at " + result.url)

        data = self.getURL(result.url)

        if data == None:
            return False

        if self.providerType == GenericProvider.NZB:
            saveDir = sickbeard.NZB_DIR
            writeMode = 'w'
        elif self.providerType == GenericProvider.TORRENT:
            saveDir = sickbeard.TORRENT_DIR
            writeMode = 'wb'
        else:
            return False

        fileName = ek.ek(os.path.join, saveDir, helpers.sanitizeFileName(result.name) + '.' + self.providerType)

        logger.log(u"Saving to " + fileName, logger.DEBUG)

        fileOut = open(fileName, writeMode)
        fileOut.write(data)
        fileOut.close()

        return True

    def searchRSS(self):
        self.cache.updateCache()
        return self.cache.findNeededEpisodes()

    def getQuality(self, item):
        title = item.findtext('title')
        quality = Quality.nameQuality(title)
        return quality

    def _doSearch(self):
        return []

    def _get_season_search_strings(self, show, season, episode=None):
        return []

    def _get_episode_search_strings(self, ep_obj):
        return []
    
    def findEpisode (self, episode, manualSearch=False):

        self._checkAuth()

        logger.log(u"Searching "+self.name+" for " + episode.prettyName(True))

        self.cache.updateCache()
        results = self.cache.searchCache(episode, manualSearch)
        logger.log(u"Cache results: "+str(results), logger.DEBUG)

        # if we got some results then use them no matter what.
        # OR
        # return anyway unless we're doing a manual search
        if results or not manualSearch:
            return results

        itemList = []

        for cur_search_string in self._get_episode_search_strings(episode):
            itemList += self._doSearch(cur_search_string)

        for item in itemList:

            title = item.findtext('title')
            url = item.findtext('link').replace('&amp;','&')

            # parse the file name
            try:
                myParser = FileParser(title)
                epInfo = myParser.parse()
            except tvnamer_exceptions.InvalidFilename:
                logger.log(u"Unable to parse the filename "+title+" into a valid episode", logger.WARNING)
                continue

            if epInfo.seasonnumber != episode.season or episode.episode not in epInfo.episodenumbers:
                logger.log("Episode "+title+" isn't "+str(episode.season)+"x"+str(episode.episode)+", skipping it", logger.DEBUG)
                continue

            quality = self.getQuality(item)

            if not episode.show.wantEpisode(epInfo.seasonnumber, epInfo.episodenumbers[0], quality, manualSearch):
                logger.log(u"Ignoring result "+title+" because we don't want an episode that is "+Quality.qualityStrings[quality], logger.DEBUG)
                continue

            logger.log(u"Found result " + title + " at " + url, logger.DEBUG)

            result = self.getResult([episode])
            result.url = url
            result.name = title
            result.quality = quality

            results.append(result)

        return results



    def findSeasonResults(self, show, season):

        itemList = []
        results = {}

        for curString in self._get_season_search_strings(show, season):
            itemList += self._doSearch(curString)

        for item in itemList:

            title = item.findtext('title')
            url = item.findtext('link')

            quality = self.getQuality(item)

            # parse the file name
            try:
                myParser = FileParser(title)
                epInfo = myParser.parse()
            except tvnamer_exceptions.InvalidFilename:
                logger.log(u"Unable to parse the filename "+title+" into a valid episode", logger.WARNING)
                continue

            if not show.is_air_by_date:
                # this check is meaningless for non-season searches
                if (epInfo.seasonnumber != None and epInfo.seasonnumber != season) or (epInfo.seasonnumber == None and season != 1):
                    logger.log(u"The result "+title+" doesn't seem to be a valid episode for season "+str(season)+", ignoring")
                    continue

                # we just use the existing info for normal searches
                actual_season = season
                actual_episodes = epInfo.episodenumbers
            
            else:
                if epInfo.seasonnumber != -1 or len(epInfo.episodenumbers) != 1:
                    logger.log(u"This is supposed to be an air-by-date search but the result "+title+" didn't parse as one, skipping it", logger.DEBUG)
                    continue
                
                myDB = db.DBConnection()
                sql_results = myDB.select("SELECT season, episode FROM tv_episodes WHERE showid = ? AND airdate = ?", [show.tvdbid, epInfo.episodenumbers[0].toordinal()])

                if len(sql_results) != 1:
                    logger.log(u"Tried to look up the date for the episode "+title+" but the database didn't give proper results, skipping it", logger.ERROR)
                    continue
                
                actual_season = int(sql_results[0]["season"])
                actual_episodes = [int(sql_results[0]["episode"])]

            # make sure we want the episode
            wantEp = True
            for epNo in actual_episodes:
                if not show.wantEpisode(actual_season, epNo, quality):
                    wantEp = False
                    break
            
            if not wantEp:
                logger.log(u"Ignoring result "+title+" because we don't want an episode that is "+Quality.qualityStrings[quality], logger.DEBUG)
                continue

            logger.log(u"Found result " + title + " at " + url, logger.DEBUG)

            # make a result object
            epObj = []
            for curEp in actual_episodes:
                epObj.append(show.getEpisode(actual_season, curEp))

            result = self.getResult(epObj)
            result.url = url
            result.name = title
            result.quality = quality

            if len(epObj) == 1:
                epNum = epObj[0].episode
            elif len(epObj) > 1:
                epNum = MULTI_EP_RESULT
                logger.log(u"Separating multi-episode result to check for later - result contains episodes: "+str(epInfo.episodenumbers), logger.DEBUG)
            elif len(epObj) == 0:
                epNum = SEASON_RESULT
                result.extraInfo = [show]
                logger.log(u"Separating full season result to check for later", logger.DEBUG)

            if epNum in results:
                results[epNum].append(result)
            else:
                results[epNum] = [result]


        return results

    def findPropers(self, date=None):

        results = self.cache.listPropers(date)

        return [classes.Proper(x['name'], x['url'], datetime.datetime.fromtimestamp(x['time'])) for x in results]


class NZBProvider(GenericProvider):

    def __init__(self, name):

        GenericProvider.__init__(self, name)

        self.providerType = GenericProvider.NZB

class TorrentProvider(GenericProvider):

    def __init__(self, name):

        GenericProvider.__init__(self, name)

        self.providerType = GenericProvider.TORRENT
