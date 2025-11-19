-- remove the earlier placeholder rows (adjust WHERE if you only want specific records gone)
DELETE FROM feedback_profile;
DELETE FROM feedback_app;

-- insert fresh sample profile feedback with valid UUID strings
INSERT INTO feedback_profile (
  id, reviewer_profile_id, reviewee_profile_id, match_id,
  overall_experience, would_meet_again, safety_feeling, respectfulness,
  headline, comment, tags
) VALUES
  ('11111111-1111-1111-1111-111111111111', 'aaaa1111-1111-1111-1111-aaaaaaaaaaaa', 'bbbb2222-2222-2222-2222-bbbbbbbbbbbb',
   'cccc3333-3333-3333-3333-cccccccccccc', 5, 1, 5, 5,
   'Great first meetup', 'Easy conversation and punctual.', '["great","punctual"]'),
  ('22222222-2222-2222-2222-222222222222', 'dddd4444-4444-4444-4444-dddddddddddd', 'bbbb2222-2222-2222-2222-bbbbbbbbbbbb',
   NULL, 3, 0, 3, 4,
   'Mixed signals', 'Good conversation but late start.', '["late-start"]');

-- insert fresh app feedback
INSERT INTO feedback_app (
  id, author_profile_id, overall, usability, reliability,
  performance, support_experience, headline, comment, tags
) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'eeee5555-5555-5555-5555-eeeeeeeeeeee',
   4, 5, 4, 4, 5, 'Love the UX', 'Everything feels smoother now.', '["praise"]'),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', NULL,
   2, 2, 3, 2, NULL, 'Slow on Android', 'Startup time regressed in the latest build.', '["bug","android"]');
