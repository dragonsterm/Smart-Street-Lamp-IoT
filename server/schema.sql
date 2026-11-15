CREATE TABLE IF NOT EXISTS devices (
    id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    last_seen_ms BIGINT NULL,
    active_boot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS boots (
    device_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    boot_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    last_seq BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (device_id, boot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS telemetry (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    device_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    boot_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    seq BIGINT UNSIGNED NOT NULL,
    received_at_ms BIGINT NOT NULL,
    payload LONGTEXT NOT NULL,
    UNIQUE KEY telemetry_sequence (device_id, boot_id, seq),
    KEY telemetry_device_time (device_id, received_at_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS commands (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    device_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    issued_at_ms BIGINT NOT NULL,
    expires_at_ms BIGINT NOT NULL,
    mode VARCHAR(8) NOT NULL,
    pwm VARCHAR(64) NOT NULL,
    auto_profile VARCHAR(16) NOT NULL DEFAULT 'adaptive',
    environment VARCHAR(12) NULL,
    status VARCHAR(16) NOT NULL,
    delivered_at_ms BIGINT NULL,
    applied_at_ms BIGINT NULL,
    detail VARCHAR(255) NULL,
    KEY commands_device_time (device_id, issued_at_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
