ALTER TABLE hosted_marketing_artifact_manifests ADD COLUMN assignment_id TEXT
    REFERENCES hosted_marketing_post_assignments(assignment_id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX hosted_marketing_artifact_assignment_output
ON hosted_marketing_artifact_manifests (assignment_id, request_id, artifact_sha256)
WHERE assignment_id IS NOT NULL;

CREATE TRIGGER hosted_marketing_artifact_assignment_insert
BEFORE INSERT ON hosted_marketing_artifact_manifests
WHEN NEW.assignment_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM hosted_marketing_post_assignments AS assignment
    WHERE assignment.assignment_id = NEW.assignment_id
      AND assignment.campaign_id = NEW.campaign_id
      AND assignment.treatment_id = NEW.treatment_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing artifact assignment lineage is invalid');
END;

DROP TRIGGER hosted_marketing_publication_assignment_insert;

CREATE TRIGGER hosted_marketing_publication_assignment_insert
BEFORE INSERT ON hosted_threads_publications
WHEN NEW.marketing_assignment_id IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM hosted_marketing_post_assignments AS assignment
         WHERE assignment.assignment_id = NEW.marketing_assignment_id
           AND assignment.candidate_id = NEW.candidate_id
           AND NOT EXISTS (
               SELECT 1 FROM hosted_marketing_artifact_requests AS request
               WHERE request.treatment_id = assignment.treatment_id
                 AND NOT EXISTS (
                     SELECT 1 FROM hosted_marketing_artifact_manifests AS manifest
                     WHERE manifest.request_id = request.request_id
                       AND manifest.treatment_id = assignment.treatment_id
                       AND manifest.assignment_id = assignment.assignment_id
                 )
           )
     )
BEGIN
    SELECT RAISE(ABORT, 'Threads publication assignment is invalid');
END;
