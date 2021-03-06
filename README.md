A conference organization web application built on Google App Engine.

This is Project 4 of Udacity Fullstack Nanodegree, and is an extension of
the code developed in the [Developing Scalable Apps in Python][7] course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.

---
## Additional funcitonality

### Conference Sessions

#### Models and design considerations

Conference `Session` type with following attributes:

1. name (string, required)
1. highlights (repeated string)
1. speakers (repeated Speaker key)
1. duration (time property)
1. typeOfSession (string, restricted to SessionType enumeration in SessionForm) 
1. date (date property)
1. startTime (time property)


Conference `SessionType` enumeration. This provides a means to limit the types of
session to a pre-defined set. Note: an obvious improvement would be to allow for this
set to be defined per-conference by the conference organizer. This could be implemented
in a future iteration.

Confernce session `Speaker` with following attributes:

1. name (string, required)

There are associated `Form` and `Forms` types for `Sesssion` and `Speaker`.

A session is bound to a single conference. This is not represented in the model itself,
but enforced in the `SessionForm`, which requires a web-safe key for a conference.


#### API end-points

The following API end-points are provided for creating and querying sessions:

* `getConferenceSessions(websafeConferenceKey)`: Given a conference, return all sessions
* `getConferenceSessionsByType(websafeConferenceKey, typeOfSession)`: Given a conference, return all sessions of a specified type (eg lecture, keynote, workshop)
* `getSessionsBySpeaker(speaker)`: Given a speaker, return all sessions given by this particular speaker, across all conferences
* `createSession(SessionForm, websafeConferenceKey)`: open to the organizer of the conference

### User session wish-list

The wish-list allows a user to maintain a list of sessions they are interested in
attending. Since users may be interested in sessions from conferences they are not
yet registered for, there is no restriction on the conferences the user can pick
sessions from. We define two additional end-points to support whsh-lists:

* `addSessionToWishlist(SessionKey)`: adds a session to the user's list of sessions of interest
* `getSessionsInWishlist()`: obtain all the sessions in a user's wish-list

#### Design considerations

Each user profile contains a wish-list of sessions. These are implemented as a repeated
string property in the `Profile` model, set in the `ProfileForm`. A user does not have
to be registered to a conference in order to add a session to the wish-list. The list
expresses an interest in, and not a commitment to, attending.


### Indices and Queries

Indices for all the required queries have been built.

#### Additional Queries

* `getConferenceSpeakers(webSafeConferenceKey)`: gets list of speakers for a given conference.
* `getConferenceByTopic(topic)`: gets list of conferences with a certain topic.

#### Query related problem

1. How would you handle a query for all non-workshop sessions before 7 pm?
2. What is the problem for implementing this query?
3. What ways to solve it did you think of?

#####Answer:

1. The query requires two inequalities: session type *not equal* to workshop and session
start time *less than* 7PM.
2. The problem is that the datastore does not support queries with inequalities on
more than one property.
3. We can query on one property, then filter the results in the application code. 
Alternatively, we can replace the `!=` condition
for the workshop session type with an `IN` of all session types except for workshop. The
latter doesn't scale well with number of session types so we opt for the former:

```python
from datetime import datetime
latest_time = '7:00 pm' # hard-wired as example. Could be e.g. request.time
sessions = Session.query().filter(
    Session.startTime < datetime.strptime(latest_time,
                                          '%I:%M %p').time()
)

return SessionForms(
    items=[self._copySessionToForm(s)
           for s in sessions if s.typeOfSession != 'Workshop']
)
```

This is implemented in method `conference.queryProblem()`, with API end-point
`queryProblem`.


### Tasks

Each time a new session is added to a conference, a "featured speaker" for that
conference is calculated. A specification of "featued speaker" is not provided, so we
select the speaker with the highest number of appearances.

The implementation uses a task queue to run a task that stores a featured speaker
message in memcache, using a per-conference key to allow for each conference to have
a featured speaker. An API end-point if provided to get the featured speaker message
for a given conference.

* API endpoint: `getFeaturedSpeaker(webSafeConferenceKey)`.
* Task URL: `/tasks/featured_speaker`.

---
[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
[7]: https://www.udacity.com/course/viewer#!/c-ud858-nd
