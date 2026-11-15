#!/usr/bin/env python3
"""Restore the saved MyPaint profile; preview unless --apply is specified."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def load_profile(profile):
    """Validate every saved file before allowing any writes."""
    profile = Path(profile).resolve()
    manifest = json.loads((profile / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not manifest.get("files"):
        raise ValueError("Неподдерживаемый или пустой манифест профиля")
    files = {}
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts
                or len(relative.parts) < 2
                or relative.parts[0] not in ("config", "data")):
            raise ValueError("Недопустимый путь в профиле: " + name)
        source = profile / relative
        if source.is_symlink() or any(p.is_symlink() for p in source.parents):
            raise ValueError("Символическая ссылка в профиле: " + name)
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Не совпала контрольная сумма: " + name)
        files[name] = content
    if not {"config/settings.json", "config/accelmap.conf"} <= files.keys():
        raise ValueError("В профиле отсутствуют настройки или горячие клавиши")
    return manifest, files


def _render_settings(content, config_dir, data_dir):
    values = {
        "${MYPAINT_DATA_DIR}": str(data_dir),
        "${MYPAINT_CONFIG_DIR}": str(config_dir),
        "${HOME}": str(Path.home()),
    }

    def expand(value):
        if isinstance(value, str):
            for token, replacement in values.items():
                value = value.replace(token, replacement)
            return value
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        return value

    settings = expand(json.loads(content.decode("utf-8")))
    return (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def restore(profile, config_dir, data_dir, apply=False, backup_root=None):
    """Overlay the profile, backing up every existing file being replaced."""
    profile = Path(profile).resolve()
    config_dir = Path(config_dir).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    roots = {"config": config_dir, "data": data_dir}
    for root in roots.values():
        if (root == Path(root.anchor) or root == Path.home()
                or root == profile or root in profile.parents
                or profile in root.parents):
            raise ValueError("Небезопасная папка назначения: " + str(root))
    if (config_dir == data_dir or config_dir in data_dir.parents
            or data_dir in config_dir.parents):
        raise ValueError("Папки настроек и данных должны быть раздельными")
    _manifest, files = load_profile(profile)
    planned = []
    for name, content in files.items():
        relative = Path(name)
        destination = roots[relative.parts[0]].joinpath(*relative.parts[1:])
        for part in (destination, *destination.parents):
            if part.is_symlink():
                raise ValueError("Ссылка в папке назначения: " + str(part))
            if part == roots[relative.parts[0]]:
                break
        if destination.exists() and not destination.is_file():
            raise ValueError("Вместо файла существует папка: " + str(destination))
        for parent in destination.parents:
            if parent.exists() and not parent.is_dir():
                raise ValueError("Вместо папки существует файл: " + str(parent))
        if name == "config/settings.json":
            content = _render_settings(content, config_dir, data_dir)
        planned.append((name, destination, content))
    if not apply:
        return {"files": len(planned), "backup": None, "applied": False}

    existing = [(name, destination) for name, destination, _ in planned
                if destination.exists()]
    backup = None
    if existing:
        backup_root = Path(backup_root or config_dir.parent / "mypaint-profile-backups")
        backup_root = backup_root.expanduser().resolve()
        for root in (config_dir, data_dir, profile):
            if backup_root == root or root in backup_root.parents:
                raise ValueError("Резервная копия должна быть вне папок профиля")
        backup = backup_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True, mode=0o700)
        for name, destination in existing:
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        (backup / "restore-targets.json").write_text(json.dumps(
            {key: str(value) for key, value in roots.items()}, indent=2
        ) + "\n", encoding="utf-8")

    for _name, destination, content in planned:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".mypaint-profile-", dir=destination.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"files": len(planned), "backup": backup, "applied": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    home = Path.home()
    parser.add_argument("--profile", type=Path,
                        default=Path(__file__).resolve().parents[1] / "personal-profile")
    parser.add_argument("--config-dir", type=Path,
                        default=Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "mypaint")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "mypaint")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--apply", action="store_true", help="применить профиль; сначала закройте MyPaint")
    args = parser.parse_args()
    try:
        result = restore(args.profile, args.config_dir, args.data_dir,
                         apply=args.apply, backup_root=args.backup_root)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "Ошибка: " + str(error) + "\n")
    print("Проверено файлов:", result["files"])
    print("Настройки:", args.config_dir.expanduser())
    print("Кисти и фоны:", args.data_dir.expanduser())
    if result["applied"]:
        print("Профиль восстановлен.")
        if result["backup"]:
            print("Прежние заменённые файлы сохранены:", result["backup"])
    else:
        print("Это проверка: файлы не изменены. Для восстановления закройте MyPaint и добавьте --apply.")


if __name__ == "__main__":
    main()
