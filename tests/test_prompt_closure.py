import copy
import json
from pathlib import Path
import unittest

from taskboard import protocol
from taskboard.protocol import ProtocolError, task_digest, validate_task


LEGACY = json.loads((Path(__file__).parent / 'fixtures/legacy-task-v1.json').read_text())


def handoff():
    return {
        'non_goals': ['Do not change the production discount implementation.'],
        'constraints': ['Keep the behavior specified in the pinned discount rules.'],
        'assumptions': [],
        'environment': 'Python 3.11 or newer, standard library only. No service credentials are needed.',
        'stop_conditions': ['If the pinned rules are missing or contradict the request, report the exact gap instead of guessing.'],
        'review': {
            'first_step': 'Read resources/discount-rules.md and src/discount.py at the pinned commit.',
            'inputs': 'The pinned source supplies src/discount.py; the declared Git resource supplies discount rules at resources/discount-rules.md.',
            'completion': 'Run the declared unittest command, then return the patch, summary, and verification record.',
            'blocking_questions': [],
        },
    }


class PromptClosureTests(unittest.TestCase):
    def test_new_publication_requires_handoff_and_an_author_review(self):
        for notes in (None, {}, {key: value for key, value in handoff().items() if key != 'review'}):
            task = copy.deepcopy(LEGACY)
            if notes is not None:
                task['handoff'] = notes
            with self.subTest(notes=notes), self.assertRaises(ProtocolError):
                protocol.validate_publication(task)

    def test_complete_declarations_preserve_task_bytes_and_digest_inputs(self):
        task = {**copy.deepcopy(LEGACY), 'handoff': handoff()}
        original = copy.deepcopy(task)
        self.assertEqual(protocol.validate_publication(task), original)
        self.assertEqual(task, original)
        self.assertNotEqual(task_digest(task), task_digest(LEGACY))

    def test_known_blockers_can_be_inspected_as_drafts_but_cannot_be_published(self):
        task = {**copy.deepcopy(LEGACY), 'handoff': handoff()}
        task['handoff']['review']['blocking_questions'] = ['The required CSV exists only on the publisher computer.']
        self.assertEqual(validate_task(task), task)
        with self.assertRaises(ProtocolError) as caught:
            protocol.validate_publication(task)
        self.assertEqual(caught.exception.code, 'PROMPT_CLOSURE_BLOCKED')

    def test_review_answers_environment_and_stop_policy_cannot_be_omitted(self):
        for field in ('first_step', 'inputs', 'completion'):
            notes = handoff()
            notes['review'][field] = ' '
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                protocol.validate_handoff(notes, for_publication=True)
        for field, value in (('environment', ''), ('stop_conditions', []), ('constraints', ['']), ('assumptions', 'none')):
            notes = handoff()
            notes[field] = value
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                protocol.validate_handoff(notes, for_publication=True)

    def test_legacy_task_remains_readable_without_inserted_defaults(self):
        self.assertEqual(validate_task(LEGACY), LEGACY)
        self.assertNotIn('handoff', validate_task(LEGACY))
        self.assertEqual(task_digest(LEGACY), 'fad5c70a87bca1621a7ad84aaec154a23d6f402170181a00aebc228b79579cc1')

    def test_handoff_text_matches_the_shared_browser_contract(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/handoff-text.json').read_text())
        for case in cases:
            for location in ('environment', 'constraints', 'first_step'):
                notes = handoff()
                if location == 'constraints':
                    notes[location] = [case['text']]
                elif location == 'first_step':
                    notes['review'][location] = case['text']
                else:
                    notes[location] = case['text']
                with self.subTest(case=case['name'], location=location):
                    if case['valid']:
                        self.assertEqual(protocol.validate_handoff(notes, for_publication=True), notes)
                    else:
                        with self.assertRaises(ProtocolError):
                            protocol.validate_handoff(notes, for_publication=True)


if __name__ == '__main__':
    unittest.main()
