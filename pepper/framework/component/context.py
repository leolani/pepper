from . import SpeechRecognitionComponent, ObjectDetectionComponent, FaceRecognitionComponent, TextToSpeechComponent
from ..context import Context
from ..sensor import UtteranceHypothesis, Face, FaceClassifier
from ..abstract import AbstractComponent, AbstractImage

from pepper.language import Utterance
from pepper.knowledge import sentences
from pepper import config

from collections import deque
from threading import Thread, Lock
from time import time

from typing import Deque, List
from random import choice

import numpy as np


class ContextComponent(AbstractComponent):

    # Minimum Distance of Person to Enter/Exit Conversation
    PERSON_AREA_ENTER = 0.25
    PERSON_AREA_EXIT = 0.2

    # Minimum Distance Difference of Person to Enter/Exit Conversation
    PERSON_DIFF_ENTER = 1.5
    PERSON_DIFF_EXIT = 1.1

    CONVERSATION_TIMEOUT = 15

    def __init__(self, backend):
        super(ContextComponent, self).__init__(backend)

        speech_comp = self.require(ContextComponent, SpeechRecognitionComponent)  # type: SpeechRecognitionComponent
        object_comp = self.require(ContextComponent, ObjectDetectionComponent)  # type: ObjectDetectionComponent
        face_comp = self.require(ContextComponent, FaceRecognitionComponent)  # type: FaceRecognitionComponent
        self.require(ContextComponent, TextToSpeechComponent)  # type: TextToSpeechComponent

        self._conversation_time = time()

        context_lock = Lock()

        self._context = Context()

        self._face_vectors = deque(maxlen=50)

        self._people_info = []
        self._face_info = []

        def on_transcript(hypotheses, audio):
            """
            Add Transcript to Chat (if a current Chat exists)

            Parameters
            ----------
            hypotheses: List[UtteranceHypothesis]
            audio: np.ndarray
            """

            with context_lock:
                if self.context.chatting and hypotheses:

                    # self.say(choice(sentences.THINKING), block=False)

                    # Add ASR Transcript to Chat as Utterance
                    utterance = self.context.chat.add_utterance(hypotheses, False)

                    # Call On Chat Turn Event
                    self.on_chat_turn(utterance)

        def get_closest_people(people):
            # type: (List[Face]) -> List[Face]

            person_area_threshold = (self.PERSON_AREA_EXIT if self.context.chatting else self.PERSON_AREA_ENTER)
            person_diff_threshold = (self.PERSON_DIFF_EXIT if self.context.chatting else self.PERSON_DIFF_ENTER)

            people_in_range = [person for person in people if person.image_bounds.area >= person_area_threshold]

            # If only one person is in range
            if len(people_in_range) == 1:

                # Return that person
                return [people_in_range[0]]

            # If multiple people are in range
            elif len(people_in_range) >= 2:

                # Sort them by proximity
                people_sorted = np.argsort([person.image_bounds.area for person in people_in_range])[::-1]

                # Identify the two closest individuals
                closest = people_in_range[people_sorted[0]]
                next_closest = people_in_range[people_sorted[1]]

                # If the closest individual is significantly closer than the next one
                if closest.image_bounds.area >= person_diff_threshold * next_closest.image_bounds.area:

                    # Return Closest Individual
                    return [closest]

                # If people are the same distance apart
                else:

                    # Return all People
                    return people_in_range
            else:
                return []

        def get_face_of_person(person, faces):
            for face in faces:
                if face.image_bounds.is_subset_of(person.image_bounds):
                    return face

        def on_image(image):
            # type: (AbstractImage) -> None
            """
            Figure out with who(m) a conversation is held and call on_chat_enter/exit appropriately

            Parameters
            ----------
            image: AbstractImage
            """

            # Get People within Conversation Bounds
            closest_people = get_closest_people(self._people_info)

            if not self.context.chatting:

                # If one person is closest and his/her face is identifiable -> Start Personal Conversation
                if len(closest_people) == 1:
                    closest_person = closest_people[0]
                    closest_face = get_face_of_person(closest_person, self._face_info)

                    if closest_face:
                        self._conversation_time = time()
                        Thread(target=self.on_chat_enter, args=(closest_face.name,)).start()
                        self._face_vectors.clear()

                # If multiple people are in range, with nobody seemingly closest -> Start Group Conversation
                elif len(closest_people) >= 2:
                    self._conversation_time = time()
                    Thread(target=self.on_chat_enter, args=(config.HUMAN_CROWD,)).start()
                    self._face_vectors.clear()

            elif self.context.chatting:

                # When talking to a human crowd
                if self.context.chat.speaker == config.HUMAN_CROWD:

                    # If still in conversation with Group, update conversation time
                    if len(closest_people) >= 2:
                        self._conversation_time = time()

                    # Else, when conversation times out
                    elif time() - self._conversation_time >= self.CONVERSATION_TIMEOUT:

                        # If a single Person enters conversation at this point -> Start conversation with them
                        if len(closest_people) == 1:
                            closest_person = closest_people[0]
                            closest_face = get_face_of_person(closest_person, self._face_info)

                            if closest_face:
                                self._conversation_time = time()
                                Thread(target=self.on_chat_enter, args=(closest_face.name,)).start()
                                self._face_vectors.clear()

                        # Otherwise, Exit Chat
                        else:
                            self.on_chat_exit()
                            self._face_vectors.clear()

                else:  # When talking to a Specific Person

                    # If still in conversation with Person, update conversation time
                    if len(closest_people) == 1:
                        closest_person = closest_people[0]
                        closest_face = get_face_of_person(closest_person, self._face_info)

                        if closest_face:

                            # If Still Chatting with Same Person -> Update Conversation Time & Face Vectors
                            if closest_face.name in [self.context.chat.speaker, FaceClassifier.NEW]:
                                self._conversation_time = time()
                                self._face_vectors.append(closest_face.representation)

                            # If Chatting to Unknown Person and Known Person Appears -> Switch Chat
                            elif self.context.chat.speaker == config.HUMAN_UNKNOWN and closest_face.name != config.HUMAN_UNKNOWN:
                                self._conversation_time = time()
                                Thread(target=self.on_chat_enter, args=(closest_face.name,)).start()
                                self._face_vectors.clear()

                    # Else, when conversation times out
                    elif time() - self._conversation_time >= self.CONVERSATION_TIMEOUT:

                        # If another Person enters conversation at this point -> Start Conversation with them
                        if len(closest_people) == 1:
                            closest_person = closest_people[0]
                            closest_face = get_face_of_person(closest_person, self._face_info)

                            if closest_face:
                                self._conversation_time = time()
                                Thread(target=self.on_chat_enter, args=(closest_face.name,)).start()
                                self._face_vectors.clear()

                        # If Group enters conversation at this point -> Start Conversation with them
                        if len(closest_people) >= 2:
                            self._conversation_time = time()
                            Thread(target=self.on_chat_enter, args=(config.HUMAN_CROWD,)).start()
                            self._face_vectors.clear()
                        else:
                            self.on_chat_exit()
                            self._face_vectors.clear()

                # Wipe face and people info after use
                self._face_info = []
                self._people_info = []

        def on_object(objects):
            # Update Context with Perceived Objects
            self.context.add_objects(objects)

            # Add Perceived People to People Info
            self._people_info = [obj for obj in objects if obj.name == "person"]

        def on_face(people):
            # Update Context with Perceived People
            self.context.add_people(people)

            # Add Perceived Faces to Face Info
            self._face_info = people

        # Link Transcript, Object and Face Events to Context
        speech_comp.on_transcript_callbacks.append(on_transcript)
        object_comp.on_object_callbacks.append(on_object)
        face_comp.on_face_callbacks.append(on_face)

        # Add On Image Callback
        self.backend.camera.callbacks.append(on_image)

    @property
    def context(self):
        # type: () -> Context
        """
        Returns
        -------
        context: Context
            Current Context
        """
        return self._context

    @property
    def face_vectors(self):
        # type: () -> Deque[np.ndarray]
        return self._face_vectors

    def say(self, text, animation=None, block=False):
        # Call super (TextToSpeechComponent)
        super(ContextComponent, self).say(text, animation, block)

        # Add Utterance to Chat
        if self.context.chatting:
            self.context.chat.add_utterance([UtteranceHypothesis(text, 1)], me=True)

    def on_chat_turn(self, utterance):
        # type: (Utterance) -> None
        """
        On Chat Turn Callback, called every time the speaker utters some Utterance

        Parameters
        ----------
        utterance: Utterance
            Utterance speaker uttered
        """
        pass

    def on_chat_enter(self, person):
        pass

    def on_chat_exit(self):
        pass
