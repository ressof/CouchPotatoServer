import os
import re
import threading
import time
import traceback

from couchpotato import get_db
from couchpotato.core.event import fireEvent, addEvent
from couchpotato.core.helpers.encoding import toUnicode, simplifyString, sp, ss
from couchpotato.core.helpers.variable import getExt, getImdb, tryInt, \
    splitString, getIdentifier
from couchpotato.core.logger import CPLog
from couchpotato.core.plugins.base import Plugin
from guessit import guess_movie_info
from subliminal.videos import Video
import enzyme
from six.moves import filter, map, zip


log = CPLog(__name__)

autoload = 'Scanner'


class Scanner(Plugin):

    ignored_in_path = [os.path.sep + 'extracted' + os.path.sep, 'extracting', '_unpack', '_failed_', '_unknown_', '_exists_', '_failed_remove_',
                       '_failed_rename_', '.appledouble', '.appledb', '.appledesktop', os.path.sep + '._', '.ds_store', 'cp.cpnfo',
                       'thumbs.db', 'ehthumbs.db', 'desktop.ini']  # unpacking, smb-crap, hidden files
    ignore_names = ['extract', 'extracting', 'extracted', 'movie', 'movies', 'film', 'films', 'download', 'downloads', 'video_ts', 'audio_ts', 'bdmv', 'certificate']
    extensions = {
        'movie': ['mkv', 'wmv', 'avi', 'mpg', 'mpeg', 'mp4', 'm2ts', 'iso', 'img', 'mdf', 'ts', 'm4v', 'flv'],
        'movie_extra': ['mds'],
        'dvd': ['vts_*', 'vob'],
        'nfo': ['nfo', 'txt', 'tag'],
        'subtitle': ['sub', 'srt', 'ssa', 'ass'],
        'subtitle_extra': ['idx'],
        'trailer': ['mov', 'mp4', 'flv']
    }

    threed_types = {
        'Half SBS': [('half', 'sbs'), ('h', 'sbs'), 'hsbs'],
        'Full SBS': [('full', 'sbs'), ('f', 'sbs'), 'fsbs'],
        'SBS': ['sbs'],
        'Half OU': [('half', 'ou'), ('h', 'ou'), 'hou'],
        'Full OU': [('full', 'ou'), ('h', 'ou'), 'fou'],
        'OU': ['ou'],
        'Frame Packed': ['mvc', ('complete', 'bluray')],
        '3D': ['3d']
    }

    file_types = {
        'subtitle': ('subtitle', 'subtitle'),
        'subtitle_extra': ('subtitle', 'subtitle_extra'),
        'trailer': ('video', 'trailer'),
        'nfo': ('nfo', 'nfo'),
        'movie': ('video', 'movie'),
        'movie_extra': ('movie', 'movie_extra'),
        'backdrop': ('image', 'backdrop'),
        'poster': ('image', 'poster'),
        'thumbnail': ('image', 'thumbnail'),
        'leftover': ('leftover', 'leftover'),
    }

    file_sizes = {  # in MB
        'movie': {'min': 200},
        'trailer': {'min': 2, 'max': 199},
        'backdrop': {'min': 0, 'max': 5},
    }

    codecs = {
        'audio': ['DTS', 'AC3', 'AC3D', 'MP3'],
        'video': ['x264', 'H264', 'DivX', 'Xvid']
    }

    resolutions = {
        '1080p': {'resolution_width': 1920, 'resolution_height': 1080, 'aspect': 1.78},
        '1080i': {'resolution_width': 1920, 'resolution_height': 1080, 'aspect': 1.78},
        '720p': {'resolution_width': 1280, 'resolution_height': 720, 'aspect': 1.78},
        '720i': {'resolution_width': 1280, 'resolution_height': 720, 'aspect': 1.78},
        '480p': {'resolution_width': 640, 'resolution_height': 480, 'aspect': 1.33},
        '480i': {'resolution_width': 640, 'resolution_height': 480, 'aspect': 1.33},
        'default': {'resolution_width': 0, 'resolution_height': 0, 'aspect': 1},
    }

    audio_codec_map = {
        0x2000: 'AC3',
        0x2001: 'DTS',
        0x0055: 'MP3',
        0x0050: 'MP2',
        0x0001: 'PCM',
        0x003: 'WAV',
        0x77a1: 'TTA1',
        0x5756: 'WAV',
        0x6750: 'Vorbis',
        0xF1AC: 'FLAC',
        0x00ff: 'AAC',
    }

    source_media = {
        'BluRay': ['bluray', 'blu-ray', 'brrip', 'br-rip'],
        'HD DVD': ['hddvd', 'hd-dvd'],
        'DVD': ['dvd'],
        'HDTV': ['hdtv']
    }

    clean = '([ _\,\.\(\)\[\]\-]|^)(3d|hsbs|sbs|half.sbs|full.sbs|ou|half.ou|full.ou|extended|extended.cut|directors.cut|french|fr|swedisch|sw|danish|dutch|nl|swesub|subs|spanish|german|ac3|dts|custom|dc|divx|divx5|dsr|dsrip|dutch|dvd|dvdr|dvdrip|dvdscr|dvdscreener|screener|dvdivx|cam|fragment|fs|hdtv|hdrip' \
            '|hdtvrip|webdl|web.dl|webrip|web.rip|internal|limited|multisubs|ntsc|ogg|ogm|pal|pdtv|proper|repack|rerip|retail|r3|r5|bd5|se|svcd|swedish|german|read.nfo|nfofix|unrated|ws|telesync|ts|telecine|tc|brrip|bdrip|video_ts|audio_ts|480p|480i|576p|576i|720p|720i|1080p|1080i|hrhd|hrhdtv|hddvd|bluray|x264|h264|xvid|xvidvd|xxx|www.www|hc|\[.*\])(?=[ _\,\.\(\)\[\]\-]|$)'
    multipart_regex = [
        '[ _\.-]+cd[ _\.-]*([0-9a-d]+)',  #*cd1
        '[ _\.-]+dvd[ _\.-]*([0-9a-d]+)',  #*dvd1
        '[ _\.-]+part[ _\.-]*([0-9a-d]+)',  #*part1
        '[ _\.-]+dis[ck][ _\.-]*([0-9a-d]+)',  #*disk1
        'cd[ _\.-]*([0-9a-d]+)$',  #cd1.ext
        'dvd[ _\.-]*([0-9a-d]+)$',  #dvd1.ext
        'part[ _\.-]*([0-9a-d]+)$',  #part1.mkv
        'dis[ck][ _\.-]*([0-9a-d]+)$',  #disk1.mkv
        '()[ _\.-]+([0-9]*[abcd]+)(\.....?)$',
        '([a-z])([0-9]+)(\.....?)$',
        '()([ab])(\.....?)$'  #*a.mkv
    ]

    cp_imdb = '\.cp\((?P<id>tt[0-9]+),?\s?(?P<random>[A-Za-z0-9]+)?\)'

    def __init__(self):

        addEvent('scanner.create_file_identifier', self.createStringIdentifier)
        addEvent('scanner.remove_cptag', self.removeCPTag)

        addEvent('scanner.scan', self.scan)
        addEvent('scanner.name_year', self.getReleaseNameYear)
        addEvent('scanner.partnumber', self.getPartNumber)

    def scan(self, folder = None, files = None, release_download = None, simple = False, newer_than = 0, return_ignored = True, check_file_date = True, on_found = None):

        folder = sp(folder)

        if not folder or not os.path.isdir(folder):
            log.error('Folder doesn\'t exists: %s', folder)
            return {}

        # Get movie "master" files
        movie_files = {}
        leftovers = []

        # Scan all files of the folder if no files are set
        if not files:
            try:
                files = []
                for root, dirs, walk_files in os.walk(folder, followlinks=True):
                    files.extend([sp(os.path.join(sp(root), ss(filename))) for filename in walk_files])

                    # Break if CP wants to shut down
                    if self.shuttingDown():
                        break

            except:
                log.error('Failed getting files from %s: %s', (folder, traceback.format_exc()))

            log.debug('Found %s files to scan and group in %s', (len(files), folder))
        else:
            check_file_date = False
            files = [sp(x) for x in files]

        for file_path in files:

            if not os.path.exists(file_path):
                continue

            # Remove ignored files
            if self.isSampleFile(file_path):
                leftovers.append(file_path)
                continue
            elif not self.keepFile(file_path):
                continue

            is_dvd_file = self.isDVDFile(file_path)
            if self.filesizeBetween(file_path, self.file_sizes['movie']) or is_dvd_file: # Minimal 300MB files or is DVD file

                # Normal identifier
                identifier = self.createStringIdentifier(file_path, folder, exclude_filename = is_dvd_file)
                identifiers = [identifier]

                # Identifier with quality
                quality = fireEvent('quality.guess', files = [file_path], size = self.getFileSize(file_path), single = True) if not is_dvd_file else {'identifier':'dvdr'}
                if quality:
                    identifier_with_quality = '%s %s' % (identifier, quality.get('identifier', ''))
                    identifiers = [identifier_with_quality, identifier]

                if not movie_files.get(identifier):
                    movie_files[identifier] = {
                        'unsorted_files': [],
                        'identifiers': identifiers,
                        'is_dvd': is_dvd_file,
                    }

                movie_files[identifier]['unsorted_files'].append(file_path)
            else:
                leftovers.append(file_path)

            # Break if CP wants to shut down
            if self.shuttingDown():
                break

        # Cleanup
        del files

        # Sort reverse, this prevents "Iron man 2" from getting grouped with "Iron man" as the "Iron Man 2"
        # files will be grouped first.
        leftovers = set(sorted(leftovers, reverse = True))

        # Group files minus extension
        ignored_identifiers = []
        for identifier, group in movie_files.items():
            if identifier not in group['identifiers'] and len(identifier) > 0: group['identifiers'].append(identifier)

            log.debug('Grouping files: %s', identifier)

            has_ignored = 0
            for file_path in list(group['unsorted_files']):
                ext = getExt(file_path)
                wo_ext = file_path[:-(len(ext) + 1)]
                found_files = set([i for i in leftovers if wo_ext in i])
                group['unsorted_files'].extend(found_files)
                leftovers = leftovers - found_files

                has_ignored += 1 if ext == 'ignore' else 0

            if has_ignored == 0:
                for file_path in list(group['unsorted_files']):
                    ext = getExt(file_path)
                    has_ignored += 1 if ext == 'ignore' else 0

            if has_ignored > 0:
                ignored_identifiers.append(identifier)

            # Break if CP wants to shut down
            if self.shuttingDown():
                break


        # Create identifiers for all leftover files
        path_identifiers = {}
        for file_path in leftovers:
            identifier = self.createStringIdentifier(file_path, folder)

            if not path_identifiers.get(identifier):
                path_identifiers[identifier] = []

            path_identifiers[identifier].append(file_path)


        # Group the files based on the identifier
        delete_identifiers = []
        for identifier, found_files in path_identifiers.items():
            log.debug('Grouping files on identifier: %s', identifier)

            group = movie_files.get(identifier)
            if group:
                group['unsorted_files'].extend(found_files)
                delete_identifiers.append(identifier)

                # Remove the found files from the leftover stack
                leftovers = leftovers - set(found_files)

            # Break if CP wants to shut down
            if self.shuttingDown():
                break

        # Cleaning up used
        for identifier in delete_identifiers:
            if path_identifiers.get(identifier):
                del path_identifiers[identifier]
        del delete_identifiers

        # Group based on folder
        delete_identifiers = []
        for identifier, found_files in path_identifiers.items():
            log.debug('Grouping files on foldername: %s', identifier)

            for ff in found_files:
                new_identifier = self.createStringIdentifier(os.path.dirname(ff), folder)

                group = movie_files.get(new_identifier)
                if group:
                    group['unsorted_files'].extend([ff])
                    delete_identifiers.append(identifier)

                    # Remove the found files from the leftover stack
                    leftovers -= leftovers - set([ff])

            # Break if CP wants to shut down
            if self.shuttingDown():
                break

        # leftovers should be empty
        if leftovers:
            log.debug('Some files are still left over: %s', leftovers)

        # Cleaning up used
        for identifier in delete_identifiers:
            if path_identifiers.get(identifier):
                del path_identifiers[identifier]
        del delete_identifiers

        # Make sure we remove older / still extracting files
        valid_files = {}
        while True and not self.shuttingDown():
            try:
                identifier, group = movie_files.popitem()
            except:
                break

            # Check if movie is fresh and maybe still unpacking, ignore files newer than 1 minute
            if check_file_date:
                files_too_new, time_string = self.checkFilesChanged(group['unsorted_files'])
                if files_too_new:
                    log.info('Files seem to be still unpacking or just unpacked (created on %s), ignoring for now: %s', (time_string, identifier))

                    # Delete the unsorted list
                    del group['unsorted_files']

                    continue

            # Only process movies newer than x
            if newer_than and newer_than > 0:
                has_new_files = False
                for cur_file in group['unsorted_files']:
                    file_time = self.getFileTimes(cur_file)
                    if file_time[0] > newer_than or file_time[1] > newer_than:
                        has_new_files = True
                        break

                if not has_new_files:
                    log.debug('None of the files have changed since %s for %s, skipping.', (time.ctime(newer_than), identifier))

                    # Delete the unsorted list
                    del group['unsorted_files']

                    continue

            valid_files[identifier] = group

        del movie_files

        total_found = len(valid_files)

        # Make sure only one movie was found if a download ID is provided
        if release_download and total_found == 0:
            log.info('Download ID provided (%s), but no groups found! Make sure the download contains valid media files (fully extracted).', release_download.get('imdb_id'))
        elif release_download and total_found > 1:
            log.info('Download ID provided (%s), but more than one group found (%s). Ignoring Download ID...', (release_download.get('imdb_id'), len(valid_files)))
            release_download = None

        # Determine file types
        processed_movies = {}
        while True and not self.shuttingDown():
            try:
                identifier, group = valid_files.popitem()
            except:
                break

            if return_ignored is False and identifier in ignored_identifiers:
                log.debug('Ignore file found, ignoring release: %s', identifier)
                total_found -= 1
                continue

            # Group extra (and easy) files first
            group['files'] = {
                'movie_extra': self.getMovieExtras(group['unsorted_files']),
                'subtitle': self.getSubtitles(group['unsorted_files']),
                'subtitle_extra': self.getSubtitlesExtras(group['unsorted_files']),
                'nfo': self.getNfo(group['unsorted_files']),
                'trailer': self.getTrailers(group['unsorted_files']),
                'leftover': set(group['unsorted_files']),
            }

            # Media files
            if group['is_dvd']:
                group['files']['movie'] = self.getDVDFiles(group['unsorted_files'])
            else:
                group['files']['movie'] = self.getMediaFiles(group['unsorted_files'])

            if len(group['files']['movie']) == 0:
                log.error('Couldn\'t find any movie files for %s', identifier)
                total_found -= 1
                continue

            log.debug('Getting metadata for %s', identifier)
            group['meta_data'] = self.getMetaData(group, folder = folder, release_download = release_download)

            # Subtitle meta
            group['subtitle_language'] = self.getSubtitleLanguage(group) if not simple else {}

            # Get parent dir from movie files
            for movie_file in group['files']['movie']:
                group['parentdir'] = os.path.dirname(movie_file)
                group['dirname'] = None

                folder_names = group['parentdir'].replace(folder, '').split(os.path.sep)
                folder_names.reverse()

                # Try and get a proper dirname, so no "A", "Movie", "Download" etc
                for folder_name in folder_names:
                    if folder_name.lower() not in self.ignore_names and len(folder_name) > 2:
                        group['dirname'] = folder_name
                        break

                break

            # Leftover "sorted" files
            for file_type in group['files']:
                if not file_type is 'leftover':
                    group['files']['leftover'] -= set(group['files'][file_type])
                    group['files'][file_type] = list(group['files'][file_type])
            group['files']['leftover'] = list(group['files']['leftover'])

            # Delete the unsorted list
            del group['unsorted_files']

            # Determine movie
            group['media'] = self.determineMedia(group, release_download = release_download)
            if not group['media']:
                log.error('Unable to determine media: %s', group['identifiers'])
            else:
                group['identifier'] = getIdentifier(group['media']) or group['media']['info'].get('imdb')

            processed_movies[identifier] = group

            # Notify parent & progress on something found
            if on_found:
                on_found(group, total_found, len(valid_files))

            # Wait for all the async events calm down a bit
            while threading.activeCount() > 100 and not self.shuttingDown():
                log.debug('Too many threads active, waiting a few seconds')
                time.sleep(10)

        if len(processed_movies) > 0:
            log.info('Found %s movies in the folder %s', (len(processed_movies), folder))
        else:
            log.debug('Found no movies in the folder %s', folder)

        return processed_movies

    def getMetaData(self, group, folder = '', release_download = None):

        data = {}
        files = list(group['files']['movie'])

        for cur_file in files:
            if not self.filesizeBetween(cur_file, self.file_sizes['movie']): continue  # Ignore smaller files

            if not data.get('audio'): # Only get metadata from first media file
                meta = self.getMeta(cur_file)

                try:
                    data['titles'] = meta.get('titles', [])
                    data['video'] = meta.get('video', self.getCodec(cur_file, self.codecs['video']))
                    data['audio'] = meta.get('audio', self.getCodec(cur_file, self.codecs['audio']))
                    data['audio_channels'] = meta.get('audio_channels', 2.0)
                    if meta.get('resolution_width'):
                        data['resolution_width'] = meta.get('resolution_width')
                        data['resolution_height'] = meta.get('resolution_height')
                        data['aspect'] = round(float(meta.get('resolution_width')) / meta.get('resolution_height', 1), 2)
                    else:
                        data.update(self.getResolution(cur_file))
                except:
                    log.debug('Error parsing metadata: %s %s', (cur_file, traceback.format_exc()))
                    pass

            data['size'] = data.get('size', 0) + self.getFileSize(cur_file)

        data['quality'] = None
        quality = fireEvent('quality.guess', size = data.get('size'), files = files, extra = data, single = True)

        # Use the quality that we snatched but check if it matches our guess
        if release_download and release_download.get('quality'):
            data['quality'] = fireEvent('quality.single', release_download.get('quality'), single = True)
            data['quality']['is_3d'] = release_download.get('is_3d', 0)
            if data['quality']['identifier'] != quality['identifier']:
                log.info('Different quality snatched than detected for %s: %s vs. %s. Assuming snatched quality is correct.', (files[0], data['quality']['identifier'], quality['identifier']))
            if data['quality']['is_3d'] != quality['is_3d']:
                log.info('Different 3d snatched than detected for %s: %s vs. %s. Assuming snatched 3d is correct.', (files[0], data['quality']['is_3d'], quality['is_3d']))

        if not data['quality']:
            data['quality'] = quality

            if not data['quality']:
                data['quality'] = fireEvent('quality.single', 'dvdr' if group['is_dvd'] else 'dvdrip', single = True)

        data['quality_type'] = 'HD' if data.get('resolution_width', 0) >= 1280 or data['quality'].get('hd') else 'SD'

        filename = re.sub(self.cp_imdb, '', files[0])
        data['group'] = self.getGroup(filename[len(folder):])
        data['source'] = self.getSourceMedia(filename)
        if data['quality'].get('is_3d', 0):
            data['3d_type'] = self.get3dType(filename)
        return data

    def get3dType(self, filename):
        filename = ss(filename)

        words = re.split('\W+', filename.lower())

        for key in self.threed_types:
            tags = self.threed_types.get(key, [])

            for tag in tags:
                if (isinstance(tag, tuple) and '.'.join(tag) in '.'.join(words)) or (isinstance(tag, (str, unicode)) and ss(tag.lower()) in words):
                    log.debug('Found %s in %s', (tag, filename))
                    return key

        return ''

    def getMeta(self, filename):

        try:
            p = enzyme.parse(filename)

            # Video codec
            vc = ('x264' if p.video[0].codec == 'AVC1' else p.video[0].codec)

            # Audio codec
            ac = p.audio[0].codec
            try: ac = self.audio_codec_map.get(p.audio[0].codec)
            except: pass

            # Find title in video headers
            titles = []

            try:
                if p.title and self.findYear(p.title):
                    titles.append(ss(p.title))
            except:
                log.error('Failed getting title from meta: %s', traceback.format_exc())

            for video in p.video:
                try:
                    if video.title and self.findYear(video.title):
                        titles.append(ss(video.title))
                except:
                    log.error('Failed getting title from meta: %s', traceback.format_exc())

            return {
                'titles': list(set(titles)),
                'video': vc,
                'audio': ac,
                'resolution_width': tryInt(p.video[0].width),
                'resolution_height': tryInt(p.video[0].height),
                'audio_channels': p.audio[0].channels,
            }
        except enzyme.exceptions.ParseError:
            log.debug('Failed to parse meta for %s', filename)
        except enzyme.exceptions.NoParserError:
            log.debug('No parser found for %s', filename)
        except:
            log.debug('Failed parsing %s', filename)

        return {}

    def getSubtitleLanguage(self, group):
        detected_languages = {}

        # Subliminal scanner
        paths = None
        try:
            paths = group['files']['movie']
            scan_result = []
            for p in paths:
                if not group['is_dvd']:
                    video = Video.from_path(sp(p))
                    video_result = [(video, video.scan())]
                    scan_result.extend(video_result)

            for video, detected_subtitles in scan_result:
                for s in detected_subtitles:
                    if s.language and s.path not in paths:
                        detected_languages[s.path] = [s.language]
        except:
            log.debug('Failed parsing subtitle languages for %s: %s', (paths, traceback.format_exc()))

        # IDX
        for extra in group['files']['subtitle_extra']:
            try:
                if os.path.isfile(extra):
                    output = open(extra, 'r')
                    txt = output.read()
                    output.close()

                    idx_langs = re.findall('\nid: (\w+)', txt)

                    sub_file = '%s.sub' % os.path.splitext(extra)[0]
                    if len(idx_langs) > 0 and os.path.isfile(sub_file):
                        detected_languages[sub_file] = idx_langs
            except:
                log.error('Failed parsing subtitle idx for %s: %s', (extra, traceback.format_exc()))

        return detected_languages

    def determineMedia(self, group, release_download = None):

        # Get imdb id from downloader
        imdb_id = release_download and release_download.get('imdb_id')
        if imdb_id:
            log.debug('Found movie via imdb id from it\'s download id: %s', release_download.get('imdb_id'))

        files = group['files']

        # Check for CP(imdb_id) string in the file paths
        if not imdb_id:
            for cur_file in files['movie']:
                imdb_id = self.getCPImdb(cur_file)
                if imdb_id:
                    log.debug('Found movie via CP tag: %s', cur_file)
                    break

        # Check and see if nfo contains the imdb-id
        nfo_file = None
        if not imdb_id:
            try:
                for nf in files['nfo']:
                    imdb_id = getImdb(nf, check_inside = True)
                    if imdb_id:
                        log.debug('Found movie via nfo file: %s', nf)
                        nfo_file = nf
                        break
            except:
                pass

        # Check and see if filenames contains the imdb-id
        if not imdb_id:
            try:
                for filetype in files:
                    for filetype_file in files[filetype]:
                        imdb_id = getImdb(filetype_file)
                        if imdb_id:
                            log.debug('Found movie via imdb in filename: %s', nfo_file)
                            break
            except:
                pass

        # Search based on identifiers
        if not imdb_id:
            for identifier in group['identifiers']:

                if len(identifier) > 2:
                    try: filename = list(group['files'].get('movie'))[0]
                    except: filename = None

                    name_year = self.getReleaseNameYear(identifier, file_name = filename if not group['is_dvd'] else None)
                    if name_year.get('name') and name_year.get('year'):
                        search_q = '%(name)s %(year)s' % name_year
                        movie = fireEvent('movie.search', q = search_q, merge = True, limit = 1)

                        # Try with other
                        if len(movie) == 0 and name_year.get('other') and name_year['other'].get('name') and name_year['other'].get('year'):
                            search_q2 = '%(name)s %(year)s' % name_year.get('other')
                            if search_q2 != search_q:
                                movie = fireEvent('movie.search', q = search_q2, merge = True, limit = 1)

                        if len(movie) > 0:
                            imdb_id = movie[0].get('imdb')
                            log.debug('Found movie via search: %s', identifier)
                            if imdb_id: break
                else:
                    log.debug('Identifier to short to use for search: %s', identifier)

        if imdb_id:
            try:
                db = get_db()
                return db.get('media', 'imdb-%s' % imdb_id, with_doc = True)['doc']
            except:
                log.debug('Movie "%s" not in library, just getting info', imdb_id)
                return {
                    'identifier': imdb_id,
                    'info': fireEvent('movie.info', identifier = imdb_id, merge = True, extended = False)
                }

        log.error('No imdb_id found for %s. Add a NFO file with IMDB id or add the year to the filename.', group['identifiers'])
        return {}

    def getCPImdb(self, string):

        try:
            m = re.search(self.cp_imdb, string.lower())
            id = m.group('id')
            if id: return id
        except AttributeError:
            pass

        return False

    def removeCPTag(self, name):
        try:
            return re.sub(self.cp_imdb, '', name).strip()
        except:
            pass
        return name

    def getSamples(self, files):
        return set(filter(lambda s: self.isSampleFile(s), files))

    def getMediaFiles(self, files):

        def test(s):
            return self.filesizeBetween(s, self.file_sizes['movie']) and getExt(s.lower()) in self.extensions['movie'] and not self.isSampleFile(s)

        return set(filter(test, files))

    def getMovieExtras(self, files):
        return set(filter(lambda s: getExt(s.lower()) in self.extensions['movie_extra'], files))

    def getDVDFiles(self, files):
        def test(s):
            return self.isDVDFile(s)

        return set(filter(test, files))

    def getSubtitles(self, files):
        return set(filter(lambda s: getExt(s.lower()) in self.extensions['subtitle'], files))

    def getSubtitlesExtras(self, files):
        return set(filter(lambda s: getExt(s.lower()) in self.extensions['subtitle_extra'], files))

    def getNfo(self, files):
        return set(filter(lambda s: getExt(s.lower()) in self.extensions['nfo'], files))

    def getTrailers(self, files):

        def test(s):
            return re.search('(^|[\W_])trailer\d*[\W_]', s.lower()) and self.filesizeBetween(s, self.file_sizes['trailer'])

        return set(filter(test, files))

    def getImages(self, files):

        def test(s):
            return getExt(s.lower()) in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'tbn']
        files = set(filter(test, files))

        images = {
            'backdrop': set(filter(lambda s: re.search('(^|[\W_])fanart|backdrop\d*[\W_]', s.lower()) and self.filesizeBetween(s, self.file_sizes['backdrop']), files))
        }

        # Rest
        images['rest'] = files - images['backdrop']

        return images


    def isDVDFile(self, file_name):

        if list(set(file_name.lower().split(os.path.sep)) & set(['video_ts', 'audio_ts'])):
            return True

        for needle in ['vts_', 'video_ts', 'audio_ts', 'bdmv', 'certificate']:
            if needle in file_name.lower():
                return True

        return False

    def keepFile(self, filename):

        # ignoredpaths
        for i in self.ignored_in_path:
            if i in filename.lower():
                log.debug('Ignored "%s" contains "%s".', (filename, i))
                return False

        # All is OK
        return True

    def isSampleFile(self, filename):
        is_sample = re.search('(^|[\W_])sample\d*[\W_]', filename.lower())
        if is_sample: log.debug('Is sample file: %s', filename)
        return is_sample

    def filesizeBetween(self, file, file_size = None):
        if not file_size: file_size = []

        try:
            return file_size.get('min', 0) < self.getFileSize(file) < file_size.get('max', 100000)
        except:
            log.error('Couldn\'t get filesize of %s.', file)

        return False

    def getFileSize(self, file):
        try:
            return os.path.getsize(file) / 1024 / 1024
        except:
            return None

    def createStringIdentifier(self, file_path, folder = '', exclude_filename = False):

        identifier = file_path.replace(folder, '').lstrip(os.path.sep) # root folder
        identifier = os.path.splitext(identifier)[0] # ext

        # Exclude file name path if needed (f.e. for DVD files)
        if exclude_filename:
            identifier = identifier[:len(identifier) - len(os.path.split(identifier)[-1])]

        # Make sure the identifier is lower case as all regex is with lower case tags
        identifier = identifier.lower()

        try:
            path_split = splitString(identifier, os.path.sep)
            identifier = path_split[-2] if len(path_split) > 1 and len(path_split[-2]) > len(path_split[-1]) else path_split[-1] # Only get filename
        except: pass

        # multipart
        identifier = self.removeMultipart(identifier)

        # remove cptag
        identifier = self.removeCPTag(identifier)

        # simplify the string
        identifier = simplifyString(identifier)

        year = self.findYear(file_path)

        # groups, release tags, scenename cleaner
        identifier = re.sub(self.clean, '::', identifier).strip(':')

        # Year
        if year and identifier[:4] != year:
            split_by = ':::' if ':::' in identifier else year
            identifier = '%s %s' % (identifier.split(split_by)[0].strip(), year)
        else:
            identifier = identifier.split('::')[0]

        # Remove duplicates
        out = []
        for word in identifier.split():
            if not word in out:
                out.append(word)

        identifier = ' '.join(out)

        return simplifyString(identifier)


    def removeMultipart(self, name):
        for regex in self.multipart_regex:
            try:
                found = re.sub(regex, '', name)
                if found != name:
                    name = found
            except:
                pass
        return name

    def getPartNumber(self, name):
        for regex in self.multipart_regex:
            try:
                found = re.search(regex, name)
                if found:
                    return found.group(1)
                return 1
            except:
                pass
        return 1

    def getCodec(self, filename, codecs):
        codecs = map(re.escape, codecs)
        try:
            codec = re.search('[^A-Z0-9](?P<codec>' + '|'.join(codecs) + ')[^A-Z0-9]', filename, re.I)
            return (codec and codec.group('codec')) or ''
        except:
            return ''

    def getResolution(self, filename):
        try:
            for key in self.resolutions:
                if key in filename.lower() and key != 'default':
                    return self.resolutions[key]
        except:
            pass

        return self.resolutions['default']

    def getGroup(self, file):
        try:
            match = re.findall('\-([A-Z0-9]+)[\.\/]', file, re.I)
            return match[-1] or ''
        except:
            return ''

    def getSourceMedia(self, file):
        for media in self.source_media:
            for alias in self.source_media[media]:
                if alias in file.lower():
                    return media

        return None

    def findYear(self, text):

        # Search year inside () or [] first
        matches = re.findall('(\(|\[)(?P<year>19[0-9]{2}|20[0-9]{2})(\]|\))', text)
        if matches:
            return matches[-1][1]

        # Search normal
        matches = re.findall('(?P<year>19[0-9]{2}|20[0-9]{2})', text)
        if matches:
            return matches[-1]

        return ''

    def getReleaseNameYear(self, release_name, file_name = None):

        release_name = release_name.strip(' .-_')

        # Use guessit first
        guess = {}
        if file_name:
            try:
                guessit = guess_movie_info(toUnicode(file_name))
                if guessit.get('title') and guessit.get('year'):
                    guess = {
                        'name': guessit.get('title'),
                        'year': guessit.get('year'),
                    }
            except:
                log.debug('Could not detect via guessit "%s": %s', (file_name, traceback.format_exc()))

        # Backup to simple
        release_name = os.path.basename(release_name.replace('\\', '/'))
        cleaned = ' '.join(re.split('\W+', simplifyString(release_name)))
        cleaned = re.sub(self.clean, ' ', cleaned)

        year = None
        for year_str in [file_name, release_name, cleaned]:
            if not year_str: continue
            year = self.findYear(year_str)
            if year:
                break

        cp_guess = {}

        if year:  # Split name on year
            try:
                movie_name = cleaned.rsplit(year, 1).pop(0).strip()
                if movie_name:
                    cp_guess = {
                        'name': movie_name,
                        'year': int(year),
                    }
            except:
                pass

        if not cp_guess:  # Split name on multiple spaces
            try:
                movie_name = cleaned.split('  ').pop(0).strip()
                cp_guess = {
                    'name': movie_name,
                    'year': int(year) if movie_name[:4] != year else 0,
                }
            except:
                pass

        if cp_guess.get('year') == guess.get('year') and len(cp_guess.get('name', '')) > len(guess.get('name', '')):
            cp_guess['other'] = guess
            return cp_guess
        elif guess == {}:
            cp_guess['other'] = guess
            return cp_guess

        guess['other'] = cp_guess
        return guess
