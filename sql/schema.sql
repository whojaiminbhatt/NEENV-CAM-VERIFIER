PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS partners (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
 type TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS partner_schemes (
 id INTEGER PRIMARY KEY, partner_id INTEGER NOT NULL REFERENCES partners(id),
 scheme_name TEXT NOT NULL, scheme_code TEXT NOT NULL, loan_type TEXT NOT NULL, description TEXT,
 status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(partner_id,scheme_code), UNIQUE(partner_id,id)
);
CREATE TABLE IF NOT EXISTS partner_cam_configuration (
 id INTEGER PRIMARY KEY, partner_id INTEGER NOT NULL REFERENCES partners(id), scheme_id INTEGER NOT NULL,
 name TEXT NOT NULL, cam_type TEXT NOT NULL, description TEXT, version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE','EXPIRED')),
 effective_from TEXT NOT NULL, effective_to TEXT, created_by TEXT NOT NULL, updated_by TEXT,
 approved_by TEXT, approved_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(partner_id,scheme_id) REFERENCES partner_schemes(partner_id,id),
 UNIQUE(partner_id,scheme_id,cam_type,version),
 CHECK(effective_to IS NULL OR effective_to>effective_from),
 CHECK((approved_by IS NULL)=(approved_at IS NULL)),
 CHECK(status='INACTIVE' OR approved_at IS NOT NULL),
 CHECK(status!='EXPIRED' OR effective_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_cam_lookup ON partner_cam_configuration(partner_id,scheme_id,cam_type,status,effective_from,effective_to);
CREATE TABLE IF NOT EXISTS cams_meta (
 id INTEGER PRIMARY KEY, configuration_id INTEGER NOT NULL REFERENCES partner_cam_configuration(id),
 meta_group TEXT NOT NULL, meta_key TEXT NOT NULL, meta_value TEXT NOT NULL CHECK(json_valid(meta_value)),
 value_type TEXT NOT NULL CHECK(value_type IN ('integer','decimal','boolean','string','array','object','null')),
 description TEXT, display_order INTEGER NOT NULL DEFAULT 0 CHECK(display_order>=0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(configuration_id,meta_group,meta_key),
 CHECK(CASE value_type WHEN 'integer' THEN json_type(meta_value)='integer' WHEN 'decimal' THEN json_type(meta_value) IN ('integer','real') WHEN 'boolean' THEN json_type(meta_value) IN ('true','false') WHEN 'string' THEN json_type(meta_value)='text' ELSE json_type(meta_value)=value_type END)
);
CREATE TRIGGER IF NOT EXISTS meta_insert_lock BEFORE INSERT ON cams_meta WHEN EXISTS(SELECT 1 FROM partner_cam_configuration WHERE id=NEW.configuration_id AND approved_at IS NOT NULL) BEGIN SELECT RAISE(ABORT,'Approved metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS meta_update_lock BEFORE UPDATE ON cams_meta WHEN EXISTS(SELECT 1 FROM partner_cam_configuration WHERE id IN (OLD.configuration_id,NEW.configuration_id) AND approved_at IS NOT NULL) BEGIN SELECT RAISE(ABORT,'Approved metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS meta_delete_lock BEFORE DELETE ON cams_meta WHEN EXISTS(SELECT 1 FROM partner_cam_configuration WHERE id=OLD.configuration_id AND approved_at IS NOT NULL) BEGIN SELECT RAISE(ABORT,'Approved metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS config_identity_lock BEFORE UPDATE ON partner_cam_configuration WHEN OLD.approved_at IS NOT NULL AND (NEW.id IS NOT OLD.id OR NEW.partner_id IS NOT OLD.partner_id OR NEW.scheme_id IS NOT OLD.scheme_id OR NEW.cam_type IS NOT OLD.cam_type OR NEW.version IS NOT OLD.version OR NEW.effective_from IS NOT OLD.effective_from OR NEW.approved_by IS NOT OLD.approved_by OR NEW.approved_at IS NOT OLD.approved_at OR NEW.name IS NOT OLD.name OR NEW.description IS NOT OLD.description) BEGIN SELECT RAISE(ABORT,'Approved configuration identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS config_delete_lock BEFORE DELETE ON partner_cam_configuration WHEN OLD.approved_at IS NOT NULL BEGIN SELECT RAISE(ABORT,'Approved configuration cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS config_overlap_insert BEFORE INSERT ON partner_cam_configuration WHEN NEW.status='ACTIVE' AND EXISTS(SELECT 1 FROM partner_cam_configuration p WHERE p.partner_id=NEW.partner_id AND p.scheme_id=NEW.scheme_id AND p.cam_type=NEW.cam_type AND p.status='ACTIVE' AND p.effective_from<coalesce(NEW.effective_to,'9999-12-31') AND NEW.effective_from<coalesce(p.effective_to,'9999-12-31')) BEGIN SELECT RAISE(ABORT,'Overlapping active configuration'); END;
CREATE TRIGGER IF NOT EXISTS config_overlap_update BEFORE UPDATE ON partner_cam_configuration WHEN NEW.status='ACTIVE' AND EXISTS(SELECT 1 FROM partner_cam_configuration p WHERE p.id<>OLD.id AND p.partner_id=NEW.partner_id AND p.scheme_id=NEW.scheme_id AND p.cam_type=NEW.cam_type AND p.status='ACTIVE' AND p.effective_from<coalesce(NEW.effective_to,'9999-12-31') AND NEW.effective_from<coalesce(p.effective_to,'9999-12-31')) BEGIN SELECT RAISE(ABORT,'Overlapping active configuration'); END;
