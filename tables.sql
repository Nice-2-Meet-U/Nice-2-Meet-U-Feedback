-- MySQL 8 schema for Nice-2-Meet-U Feedback Microservice
-- Aligns with FastAPI models in main.py (CHAR(36) UUIDs, JSON tags, auto-updated timestamps)

CREATE TABLE IF NOT EXISTS feedback_profile (
  id CHAR(36) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  reviewer_profile_id CHAR(36) NOT NULL,
  reviewee_profile_id CHAR(36) NOT NULL,
  match_id CHAR(36) NULL,

  overall_experience TINYINT NOT NULL,
  would_meet_again TINYINT NULL,
  safety_feeling TINYINT NULL,
  respectfulness TINYINT NULL,

  headline VARCHAR(120) NULL,
  comment TEXT NULL,
  tags JSON NULL,

  PRIMARY KEY (id),
  UNIQUE KEY uq_feedback_profile_match_reviewer (match_id, reviewer_profile_id),
  KEY ix_feedback_profile_reviewee_created (reviewee_profile_id, created_at),
  KEY ix_feedback_profile_reviewer_created (reviewer_profile_id, created_at),
  KEY ix_feedback_profile_match (match_id),
  KEY ix_feedback_profile_overall (overall_experience)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS feedback_app (
  id CHAR(36) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  author_profile_id CHAR(36) NULL,

  overall TINYINT NOT NULL,
  usability TINYINT NULL,
  reliability TINYINT NULL,
  performance TINYINT NULL,
  support_experience TINYINT NULL,

  headline VARCHAR(120) NULL,
  comment TEXT NULL,
  tags JSON NULL,

  PRIMARY KEY (id),
  KEY ix_feedback_app_created (created_at),
  KEY ix_feedback_app_author (author_profile_id),
  KEY ix_feedback_app_overall (overall)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
