import assert from 'node:assert/strict';
import test from 'node:test';
import {exactLinkedLesson} from './constructor-linked-lesson.ts';

test('the review link never opens the previous selection, first lesson, or a different draft', () => {
  const selected = {id: 'tue-math'};
  const target = {id: 'wed-elective'};
  const link = {draftId:'working',active:target.id};
  assert.equal(exactLinkedLesson(link, 'working', [selected,target]), target);
  assert.equal(exactLinkedLesson(link, 'working', [selected]), undefined);
  assert.equal(exactLinkedLesson(link, 'working', [selected,target,target]), undefined);
  assert.equal(exactLinkedLesson(link, 'copy', [selected,target]), undefined);
});
