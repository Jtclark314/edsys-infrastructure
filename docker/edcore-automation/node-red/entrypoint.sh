#!/bin/sh
set -eu

project_name=edcore-automation
project_dir="/data/projects/$project_name"

command -v git >/dev/null 2>&1 || {
  echo "The pinned Node-RED image does not provide git; Projects cannot start." >&2
  exit 1
}

mkdir -p /data/projects
new_project=0
if [ ! -d "$project_dir" ]; then
  mkdir -p "$project_dir"
  cp -a /opt/edsys/project-seed/. "$project_dir/"
  new_project=1
elif [ ! -d "$project_dir/.git" ]; then
  echo "$project_dir exists but is not Git-backed; refusing to overwrite it." >&2
  exit 1
fi

# Node-RED Projects do not fall back to the global key when a Project's
# credentialSecret is false. Seed/verify the active Project with the exact key
# read from the root-owned Compose secret without printing it.
NEW_PROJECT="$new_project" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const path = "/data/.config.projects.json";
const credentialsPath = "/data/projects/edcore-automation/flows_cred.json";
const secret = fs.readFileSync("/run/secrets/node_red_credential_secret", "utf8").trim();
if (!secret) throw new Error("Node-RED credential secret is empty");
if (fs.existsSync(path)) {
  const config = JSON.parse(fs.readFileSync(path, "utf8"));
  const configured = config.projects && config.projects["edcore-automation"] && config.projects["edcore-automation"].credentialSecret;
  if (configured !== secret || config.activeProject !== "edcore-automation") {
    throw new Error("Active Project credential encryption does not match the mounted secret");
  }
} else {
  const config = {projects:{"edcore-automation":{credentialSecret:secret}},activeProject:"edcore-automation"};
  const temporary = `${path}.new`;
  fs.writeFileSync(temporary, JSON.stringify(config), {encoding:"utf8", mode:0o600});
  fs.renameSync(temporary, path);
}
fs.chmodSync(path, 0o600);

const key = crypto.createHash("sha256").update(secret).digest();
const current = JSON.parse(fs.readFileSync(credentialsPath, "utf8"));
if (Object.keys(current).length === 0) {
  if (process.env.NEW_PROJECT !== "1") {
    throw new Error("Existing Project credentials are unencrypted; explicit migration is required");
  }
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv("aes-256-ctr", key, iv);
  const encrypted = iv.toString("hex") + cipher.update("{}", "utf8", "base64") + cipher.final("base64");
  fs.writeFileSync(credentialsPath, JSON.stringify({"$":encrypted}) + "\n", {encoding:"utf8", mode:0o600});
} else {
  if (Object.keys(current).length !== 1 || typeof current.$ !== "string" || current.$.length < 33) {
    throw new Error("Project credentials file is not encrypted");
  }
  const iv = Buffer.from(current.$.substring(0, 32), "hex");
  const decipher = crypto.createDecipheriv("aes-256-ctr", key, iv);
  const cleartext = decipher.update(current.$.substring(32), "base64", "utf8") + decipher.final("utf8");
  JSON.parse(cleartext);
}
NODE

if [ "$new_project" -eq 1 ]; then
  git -C "$project_dir" init --initial-branch=main >/dev/null
  git -C "$project_dir" config user.name "EdSys Automation"
  git -C "$project_dir" config user.email "automation@example.invalid"
  git -C "$project_dir" add --all
  git -C "$project_dir" commit --no-gpg-sign -m "Seed reviewed EdCore automation project" >/dev/null
else
  [ -z "$(git -C "$project_dir" status --porcelain)" ] || {
    echo "Existing Node-RED Project is dirty; refusing a managed seed update." >&2
    exit 1
  }
  subject=$(git -C "$project_dir" log -1 --pretty=%s)
  case "$subject" in
    "Seed reviewed EdCore automation project"|"Update reviewed EdCore automation project to "*) ;;
    *)
      echo "Existing Node-RED Project has unmanaged commits; explicit review/merge is required." >&2
      exit 1
      ;;
  esac
  for managed in .edsys-release .gitignore README.md flows.json package.json; do
    cp -a "/opt/edsys/project-seed/$managed" "$project_dir/$managed"
  done
  if [ -n "$(git -C "$project_dir" status --porcelain)" ]; then
    release=$(cat /opt/edsys/project-seed/.edsys-release)
    git -C "$project_dir" add .edsys-release .gitignore README.md flows.json package.json
    git -C "$project_dir" commit --no-gpg-sign \
      -m "Update reviewed EdCore automation project to $release" >/dev/null
  fi
fi

rm -f /data/.edsys-health/mqtt.status
exec npm start -- --userDir /data --settings /opt/edsys/settings.js
