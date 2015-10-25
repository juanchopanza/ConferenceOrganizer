#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.appengine.api import memcache

from models import StringMessage
from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionType
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

CONFERENCE_DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

SESSION_DEFAULTS = {
    "duration": "01:00",
    "typeOfSession": SessionType("NOT_SPECIFIED"),
    "date": "1970-01-01",
    "startTime": "09:00"
}

OPERATORS = {
    'EQ':   '=',
    'GT':   '>',
    'GTEQ': '>=',
    'LT':   '<',
    'LTEQ': '<=',
    'NE':   '!='
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

CONF_BY_TOPIC_REQUEST = endpoints.ResourceContainer(
    topic=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_BY_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    typeOfSession=messages.EnumField(SessionType, 1),
    websafeConferenceKey=messages.StringField(2),
)

SESSION_WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1',
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in CONFERENCE_DEFAULTS:
            if data[df] in (None, []):
                data[df] = CONFERENCE_DEFAULTS[df]
                setattr(request, df, CONFERENCE_DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10],
                                                  "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10],
                                                "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(
            params={'email': user.email(),
                    'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


# - - - Session objects - - - - - - - - - - - - - - - - - -

    def _createSessionObject(self, request):
        '''Create a new session object

        Note:
            Only conference owner can add sessions
        '''
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # get conference using websafe key
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        # Raise if conference doesn't exist
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can add a session to the conference.')

        # Check request has session required attribute name
        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['websafeConferenceKey']

        # add default values for those missing (both data model & outbound Message)
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
                setattr(request, df, SESSION_DEFAULTS[df])

        # convert session type to string
        if data['typeOfSession']:
            data['typeOfSession'] = str(data['typeOfSession'])

        # convert dates from strings to Date and TimeDuration objects
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10],
                                             "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5],
                                                  "%H:%M").time()
        if data['duration']:
            data['duration'] = datetime.strptime(data['duration'][:5],
                                                 "%H:%M").time()

        # Transform list of speaker names into list of speaker keys
        # Speaker names are case-insensitive
        data['speakers'] = [
            Speaker.get_or_insert(speaker.lower().strip(),
                                  name=speaker).key
            for speaker in data['speakers']
        ]

        # get Conference Key, allocate Session ID
        c_key = conf.key
        s_id = Session.allocate_ids(size=1, parent=c_key)[0]

        # create Session's key and store in data
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data['key'] = s_key

        # creation of Session & return (modified) SessionForm
        Session(**data).put()
        return self._copySessionToForm(s_key.get())

    def _copySessionToForm(self, session):
        """Copies relevant fields from a Session to a SessionForm.
        """
        # copy relevant fields from Session to SessionForm
        session_form = SessionForm()
        for field in session_form.all_fields():
            if hasattr(session, field.name):
                if field.name in ('date', 'startTime', 'duration'):
                    setattr(session_form, field.name,
                            str(getattr(session, field.name)))
                elif field.name == 'typeOfSession':
                    setattr(session_form, field.name,
                            getattr(SessionType, getattr(session, field.name)))
                elif field.name == 'speakers':
                    setattr(session_form, field.name,
                            [str(s.get().name) for s in session.speakers])
                else:
                    setattr(session_form, field.name,
                            getattr(session, field.name))
            elif field.name == "websafeKey":
                setattr(session_form, field.name,
                        session.key.urlsafe())
            elif field.name == "websafeConferenceKey":
                setattr(session_form, field.name,
                        session.key.parent().urlsafe())

        session_form.check_initialized()

        return session_form

    def _getConferenceSessions(self, request):
        '''Given a conference, return all its sessions.'''

        # get conference key from websafe key
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s'
                % request.websafeConferenceKey)

        # create and return ancestor query for this user
        return Session.query(ancestor=conf.key)

    def _getSessionsBySpeaker(self, request):
        '''Given a speaker, return all sessions given \
        by this particular speaker, across all conferences
        '''
        if not request.name:
            raise endpoints.BadRequestException(
                "Speaker 'name' field required"
            )

        speaker = Speaker.query(Speaker.name == request.name).get()
        speaker_key = speaker.key if speaker is not None else None
        return Session.query(Session.speakers == speaker_key)

    # This modifies an existing resource. Although only a single user can
    # modify, it is marked transactional to avoid the risk of race conditions.
    @ndb.transactional()
    def _addSessionToWishlist(self, request):
        '''Add a session key to a user's wishlist.

        Returns:
            BooleanMessage True if session added, False otherwise.
        '''
        prof = self._getProfileFromUser()  # get user Profile

        # Check if session with right websafe session key exists
        # and raise if it doesn't
        ws_key = request.websafeSessionKey
        session_key = ndb.Key(urlsafe=ws_key).get()
        if not session_key:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % ws_key)

        # check if session is already on the user wishlist
        if ws_key in prof.wishListSessionKeys:
            return BooleanMessage(data=False)

        # add session to profile's withlist
        prof.wishListSessionKeys.append(ws_key)

        # write modified profile back to the datastore & return
        prof.put()
        return BooleanMessage(data=True)


    def _getSessionsInWishlist(self, request):
        '''Get a list of sessions for all sessions in a user's wish-list'''

        prof = self._getProfileFromUser()

        # get sessionsKeysOnWishlist from profile.
        sessions = [ndb.Key(urlsafe=key) for key in
                    prof.wishListSessionKeys]

        return ndb.get_multi(sessions)

    #====== End points =========================================================

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id =  getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )

    @endpoints.method(CONF_BY_TOPIC_REQUEST, ConferenceForms,
                      path='conference/by_topic/{topic}',
                      http_method='GET', name='getConferencesByTopic')
    def getConferencesByTopic(self, request):
        """ Returns all conferences with a certain topic."""
        # check request has  topic field
        if not request.topic:
            raise endpoints.BadRequestException("Conference 'topic' field \
                required")
        # get all conferences filtered by topic and order them by name
        confs = Conference.query().filter(
            Conference.topics.IN([request.topic])).order(Conference.name)
        # return set of ConferenceForms
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in confs]
        )

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConferenceKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf,
                                              names[conf.organizerUserId])
                   for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

    @endpoints.method(SESSION_GET_REQUEST, SpeakerForms,
                      path='conference/{websafeConferenceKey}/speakers',
                      http_method='GET', name='getConferenceSpeakers')
    def getConferenceSpeakers(self, request):
        '''Given a conference, return all speakers'''
        sessions = self._getConferenceSessions(request)
        speakers = [speaker.get() for session in sessions
                    for speaker in session.speakers]
        return SpeakerForms(
            items=[SpeakerForm(name=speaker.name) for speaker in speakers]
        )

# - - - Sessions - - - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        '''Given a conference, return all sessions'''
        sessions = self._getConferenceSessions(request)
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SESSION_BY_TYPE_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions/by_type',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        '''Get all the sessions of a certain type in a conference'''
        # Get all conference sessions filtered by typeOfSession
        sessions = self._getConferenceSessions(request).filter(
            Session.typeOfSession == str(request.typeOfSession))
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SpeakerForm, SessionForms,
                      path='sessions/by_speaker',
                      http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        '''Given a speaker, return all sessions given \
        by this particular speaker, across all conferences
        '''
        sessions = self._getSessionsBySpeaker(request)
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """ Creates a new session for a conference."""
        return self._createSessionObject(request)


    @endpoints.method(SESSION_WISHLIST_POST_REQUEST, BooleanMessage,
                      path='wishlist',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        ''' Add a session to user's wish-list.

        Take no action if session is already on list.
        '''
        return self._addSessionToWishlist(request)


    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='wishlist',
                      http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        '''Get list of sessions in user's wish-list'''
        sessions = self._getSessionsInWishlist(request)
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sessions]
        )

# - - - Query problem - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='_query_problem', http_method='GET',
                      name='queryProblem')
    def queryProblem(self, request):
        '''Solution to the query problem

        Get sessions that aren't workshops and that start before 7PM.

        Note:
            Since the latest time and session type are hard-wired, this \
            isn't a very useful query. In a real-life application we would \
            consider making this a function of a session type blacklist \
            and a time parameter.
        '''
        latest_time = '7:00 pm'
        sessions = Session.query().filter(
            Session.startTime < datetime.strptime(latest_time,
                                                  '%I:%M %p').time()
        )

        return SessionForms(
            items=[self._copySessionToForm(s)
                   for s in sessions if s.typeOfSession != 'Workshop']
        )

# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(
            ndb.AND(
                Conference.seatsAvailable <= 5,
                Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if announcement is None:
            announcement = ""
        return StringMessage(data=announcement)


api = endpoints.api_server([ConferenceApi]) # register API
