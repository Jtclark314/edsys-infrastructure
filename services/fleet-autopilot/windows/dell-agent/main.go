//go:build windows

package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const version = "0.2.0"

type config struct {
	HubURL              string `json:"hub_url"`
	AgentID             string `json:"agent_id"`
	HostID              string `json:"host_id"`
	StateRoot           string `json:"state_root"`
	BundleRoot          string `json:"bundle_root"`
	TrustedSignerSHA256 string `json:"trusted_signer_sha256"`
	AllowMutations      bool   `json:"allow_mutations"`
	HeartbeatSecond     int    `json:"heartbeat_seconds"`
}

type agent struct {
	config     config
	privateKey ed25519.PrivateKey
	client     *http.Client
}

type command struct {
	ID           string         `json:"id"`
	Action       string         `json:"action"`
	Component    string         `json:"component"`
	Manifest     map[string]any `json:"manifest"`
	ManifestHash string         `json:"manifest_hash"`
	ExpiresAt    string         `json:"expires_at"`
}

type pollResponse struct {
	Commands []command `json:"commands"`
}

func main() {
	configPath := flag.String("config", filepath.Join(os.Getenv("ProgramData"), "EdSys", "FleetAgent", "config.json"), "agent config")
	printEnrollment := flag.Bool("print-enrollment", false, "print the public enrollment record and exit")
	once := flag.Bool("once", false, "send one heartbeat/poll cycle and exit")
	flag.Parse()

	cfg, err := loadConfig(*configPath)
	if err != nil {
		fatal(err)
	}
	identity, public, err := loadOrCreateIdentity(cfg.StateRoot)
	if err != nil {
		fatal(err)
	}
	fingerprint := sha256.Sum256(public)
	if *printEnrollment {
		value := map[string]any{
			"agent_id": cfg.AgentID, "host_id": cfg.HostID,
			"public_key":    base64.StdEncoding.EncodeToString(public),
			"fingerprint":   "sha256:" + base64.RawURLEncoding.EncodeToString(fingerprint[:]),
			"agent_version": version,
		}
		encoded, _ := json.MarshalIndent(value, "", "  ")
		fmt.Println(string(encoded))
		return
	}

	a := &agent{
		config: cfg, privateKey: identity,
		client: &http.Client{Timeout: 45 * time.Second},
	}
	if *once {
		if err := a.cycle(context.Background()); err != nil {
			fatal(err)
		}
		return
	}
	interval := time.Duration(cfg.HeartbeatSecond) * time.Second
	if interval < 30*time.Second {
		interval = 60 * time.Second
	}
	for {
		if err := a.cycle(context.Background()); err != nil {
			fmt.Fprintf(os.Stderr, "%s cycle failed: %v\n", time.Now().Format(time.RFC3339), err)
		}
		time.Sleep(interval)
	}
}

func loadConfig(path string) (config, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return config{}, err
	}
	var value config
	if err := json.Unmarshal(content, &value); err != nil {
		return config{}, err
	}
	value.HubURL = strings.TrimRight(value.HubURL, "/")
	if !strings.HasPrefix(value.HubURL, "https://") || value.AgentID == "" || value.HostID == "" {
		return config{}, errors.New("hub_url must be Tailnet HTTPS and agent_id/host_id are required")
	}
	if value.StateRoot == "" {
		value.StateRoot = filepath.Join(os.Getenv("ProgramData"), "EdSys", "FleetAgent")
	}
	if value.BundleRoot == "" {
		value.BundleRoot = filepath.Join(value.StateRoot, "bundle")
	}
	return value, nil
}

func loadOrCreateIdentity(root string) (ed25519.PrivateKey, ed25519.PublicKey, error) {
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, nil, err
	}
	path := filepath.Join(root, "identity.dpapi")
	if encrypted, err := os.ReadFile(path); err == nil {
		plain, err := unprotectPrivateKey(encrypted)
		if err != nil {
			return nil, nil, err
		}
		if len(plain) != ed25519.PrivateKeySize {
			return nil, nil, errors.New("stored Ed25519 identity has an invalid length")
		}
		private := ed25519.PrivateKey(plain)
		return private, private.Public().(ed25519.PublicKey), nil
	}
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	encrypted, err := protectPrivateKey(private)
	if err != nil {
		return nil, nil, err
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, encrypted, 0600); err != nil {
		return nil, nil, err
	}
	if err := os.Rename(temporary, path); err != nil {
		return nil, nil, err
	}
	return private, public, nil
}

func (a *agent) cycle(ctx context.Context) error {
	status := a.inventory(ctx)
	body, _ := json.Marshal(status)
	var heartbeat map[string]any
	if err := a.request(ctx, http.MethodPost, "/api/fleet/agents/"+a.config.AgentID+"/heartbeat", body, &heartbeat); err != nil {
		return err
	}
	var poll pollResponse
	if err := a.request(ctx, http.MethodPost, "/api/fleet/agents/"+a.config.AgentID+"/poll", body, &poll); err != nil {
		return err
	}
	for _, item := range poll.Commands {
		result, runErr := a.execute(ctx, item)
		payload := map[string]any{"status": "complete", "result": result}
		if runErr != nil {
			payload = map[string]any{"status": "failed", "error": runErr.Error(), "result": result}
		}
		encoded, _ := json.Marshal(payload)
		var reply map[string]any
		if err := a.request(ctx, http.MethodPost, "/api/fleet/agents/"+a.config.AgentID+"/commands/"+item.ID+"/result", encoded, &reply); err != nil {
			return err
		}
	}
	return nil
}

func (a *agent) request(ctx context.Context, method, path string, body []byte, output any) error {
	hash := sha256.Sum256(body)
	nonceBytes := make([]byte, 24)
	if _, err := rand.Read(nonceBytes); err != nil {
		return err
	}
	nonce := base64.RawURLEncoding.EncodeToString(nonceBytes)
	timestamp := fmt.Sprintf("%d", time.Now().Unix())
	canonical := strings.Join([]string{method, path, timestamp, nonce, hex.EncodeToString(hash[:])}, "\n")
	signature := ed25519.Sign(a.privateKey, []byte(canonical))
	request, err := http.NewRequestWithContext(ctx, method, a.config.HubURL+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-EdSys-Agent", a.config.AgentID)
	request.Header.Set("X-EdSys-Timestamp", timestamp)
	request.Header.Set("X-EdSys-Nonce", nonce)
	request.Header.Set("X-EdSys-Body-SHA256", hex.EncodeToString(hash[:]))
	request.Header.Set("X-EdSys-Signature", base64.StdEncoding.EncodeToString(signature))
	response, err := a.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	content, err := io.ReadAll(io.LimitReader(response.Body, 2<<20))
	if err != nil {
		return err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("hub returned HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(content)))
	}
	if output != nil && len(content) > 0 {
		return json.Unmarshal(content, output)
	}
	return nil
}

func (a *agent) inventory(ctx context.Context) map[string]any {
	admin := commandOutput(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
	return map[string]any{
		"agent_version":     version,
		"host_id":           a.config.HostID,
		"hostname":          hostname(),
		"platform":          runtime.GOOS,
		"architecture":      runtime.GOARCH,
		"mutations_allowed": a.config.AllowMutations,
		"versions": map[string]any{
			"windows":   commandOutput(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "[Environment]::OSVersion.Version.ToString()"),
			"codex":     commandOutput(ctx, "codex.exe", "--version"),
			"node":      commandOutput(ctx, "node.exe", "--version"),
			"npm":       commandOutput(ctx, "npm.cmd", "--version"),
			"chrome":    commandOutput(ctx, `C:\Program Files\Google\Chrome\Application\chrome.exe`, "--version"),
			"tailscale": commandOutput(ctx, "tailscale.exe", "version"),
			"docker":    commandOutput(ctx, "docker.exe", "version", "--format", "{{.Client.Version}}"),
		},
		"local_admin": strings.EqualFold(strings.TrimSpace(admin), "true"),
		"observed_at": time.Now().UTC().Format(time.RFC3339Nano),
	}
}

func (a *agent) execute(ctx context.Context, item command) (map[string]any, error) {
	encoded, _ := json.Marshal(item.Manifest)
	hash := sha256.Sum256(encoded)
	if hex.EncodeToString(hash[:]) != item.ManifestHash {
		return nil, errors.New("command manifest hash mismatch")
	}
	expires, err := time.Parse(time.RFC3339Nano, item.ExpiresAt)
	if err != nil || !time.Now().Before(expires) {
		return nil, errors.New("command approval expired")
	}
	kind, _ := item.Manifest["kind"].(string)
	switch kind {
	case "inventory":
		return a.inventory(ctx), nil
	case "node-toolchain-phase":
		if !a.config.AllowMutations {
			return nil, errors.New("corporate policy has not enabled Fleet mutations on this laptop")
		}
		return a.nodePhase(ctx, item.Manifest)
	case "node-toolchain-transaction":
		if !a.config.AllowMutations {
			return nil, errors.New("corporate policy has not enabled Fleet mutations on this laptop")
		}
		return a.nodeTransaction(ctx, item.Manifest)
	case "guarded-component-transaction":
		if !a.config.AllowMutations {
			return nil, errors.New("corporate policy has not enabled Fleet mutations on this laptop")
		}
		return a.guardedTransaction(ctx, item.Manifest)
	case "capability-benchmark":
		return a.benchmark(ctx)
	default:
		return nil, errors.New("command kind is not allowlisted by the signed agent")
	}
}

func (a *agent) guardedTransaction(ctx context.Context, manifest map[string]any) (map[string]any, error) {
	action, _ := manifest["action"].(string)
	if action != "upgrade" && action != "rollback" {
		return nil, errors.New("guarded transaction action is not allowlisted")
	}
	mutationPhase := "apply"
	verificationPhase := "verify"
	if action == "rollback" {
		mutationPhase = "rollback_selected"
		verificationPhase = "verify_rollback"
	}
	steps := []string{"discover", "resolve_candidate", "preflight", "checkpoint", mutationPhase, "restart_or_reboot", verificationPhase}
	qualification, _ := manifest["qualification"].(bool)
	if qualification {
		if action != "upgrade" {
			return nil, errors.New("adapter qualification requires an upgrade rollback/reapply rehearsal")
		}
		steps = append(steps, "rollback", "verify_rollback", "apply", "restart_or_reboot", "verify")
	}
	steps = append(steps, "accept", "cleanup")
	results := make([]map[string]any, 0, len(steps)+1)
	mutationAttempted := false
	for _, phase := range steps {
		if phase == mutationPhase || (qualification && phase == "apply") {
			mutationAttempted = true
		}
		value, err := a.guardedPhase(ctx, manifest, phase)
		label := phaseLabel(phase)
		results = append(results, map[string]any{"phase": label, "result": value, "passed": err == nil})
		if err != nil {
			if mutationAttempted && phase != "rollback" {
				safetyPhase := "rollback"
				verifyPhase := "verify_rollback"
				if action == "rollback" {
					safetyPhase = "restore_checkpoint"
					verifyPhase = "verify"
				}
				rollback, rollbackErr := a.guardedPhase(ctx, manifest, safetyPhase)
				results = append(results, map[string]any{"phase": "AutomaticRollback", "action": safetyPhase, "result": rollback, "passed": rollbackErr == nil})
				if rollbackErr != nil {
					return map[string]any{"phases": results, "automatic_rollback": "failed"}, fmt.Errorf("%v; automatic rollback failed: %v", err, rollbackErr)
				}
				restarted, restartErr := a.guardedPhase(ctx, manifest, "restart_or_reboot")
				results = append(results, map[string]any{"phase": "AutomaticRollbackRestart", "result": restarted, "passed": restartErr == nil})
				if restartErr != nil {
					return map[string]any{"phases": results, "automatic_rollback": "failed"}, fmt.Errorf("%v; automatic rollback restart failed: %v", err, restartErr)
				}
				verified, verifyErr := a.guardedPhase(ctx, manifest, verifyPhase)
				results = append(results, map[string]any{"phase": "AutomaticRollbackVerify", "result": verified, "passed": verifyErr == nil})
				if verifyErr != nil {
					return map[string]any{"phases": results, "automatic_rollback": "failed"}, fmt.Errorf("%v; automatic rollback verification failed: %v", err, verifyErr)
				}
				return map[string]any{"phases": results, "automatic_rollback": "passed"}, err
			}
			return map[string]any{"phases": results}, err
		}
	}
	return map[string]any{"phases": results, "qualification_rehearsed": qualification}, nil
}

func (a *agent) guardedPhase(ctx context.Context, manifest map[string]any, phase string) (map[string]any, error) {
	if err := a.verifyBundle(ctx); err != nil {
		return nil, err
	}
	adapterManifest, ok := manifest["adapter_manifest"].(map[string]any)
	if !ok {
		return nil, errors.New("guarded transaction is missing adapter_manifest")
	}
	if value, _ := adapterManifest["adapter"].(string); value == "" {
		return nil, errors.New("guarded adapter name is missing")
	}
	for _, name := range []string{"candidate", "rollback"} {
		artifact, ok := adapterManifest[name].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("guarded %s manifest is missing", name)
		}
		digest, _ := artifact["sha256"].(string)
		source, _ := artifact["source"].(string)
		if !validSHA256(digest) || !(strings.HasPrefix(source, "https://") || strings.HasPrefix(source, "git+") || strings.HasPrefix(source, "private://")) {
			return nil, fmt.Errorf("guarded %s artifact identity is invalid", name)
		}
	}
	phases, ok := adapterManifest["phases"].(map[string]any)
	if !ok {
		return nil, errors.New("guarded phase registry is missing")
	}
	for _, required := range []string{"discover", "resolve_candidate", "preflight", "checkpoint", "apply", "restart_or_reboot", "verify", "accept", "rollback", "cleanup"} {
		if _, exists := phases[required]; !exists {
			return nil, fmt.Errorf("guarded lifecycle phase is absent: %s", required)
		}
	}
	if _, exists := phases["verify_rollback"]; !exists {
		return nil, errors.New("guarded lifecycle phase is absent: verify_rollback")
	}
	if action, _ := manifest["action"].(string); action == "rollback" {
		for _, required := range []string{"rollback_selected", "restore_checkpoint"} {
			if _, exists := phases[required]; !exists {
				return nil, fmt.Errorf("guarded rollback phase is absent: %s", required)
			}
		}
	}
	specification, ok := phases[phase].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("guarded phase is invalid: %s", phase)
	}
	if noop, _ := specification["noop"].(bool); noop {
		if phase == "apply" || phase == "rollback" {
			return nil, fmt.Errorf("guarded mutating phase cannot be a no-op: %s", phase)
		}
		reason, _ := specification["reason"].(string)
		if len(strings.TrimSpace(reason)) < 12 {
			return nil, errors.New("guarded no-op requires a concrete reason")
		}
		return map[string]any{"phase": phase, "noop": true, "reason": reason}, nil
	}
	rawArgv, ok := specification["argv"].([]any)
	if !ok || len(rawArgv) == 0 {
		return nil, fmt.Errorf("guarded phase has no exact argv: %s", phase)
	}
	argv := make([]string, 0, len(rawArgv))
	for _, raw := range rawArgv {
		value, ok := raw.(string)
		if !ok || value == "" || strings.ContainsRune(value, '\x00') {
			return nil, errors.New("guarded phase argv is invalid")
		}
		argv = append(argv, value)
	}
	timeout := 30 * time.Minute
	if seconds, ok := specification["timeout_seconds"].(float64); ok && seconds >= 1 && seconds <= 3600 {
		timeout = time.Duration(seconds) * time.Second
	}
	output, err := run(ctx, timeout, argv[0], argv[1:]...)
	evidence := map[string]any{"phase": phase, "output": truncate(output, 6000)}
	if err != nil {
		return evidence, err
	}
	if required, _ := specification["stdout_contains"].(string); required != "" && !strings.Contains(output, required) {
		return evidence, errors.New("guarded phase acceptance text was absent")
	}
	if phase == "checkpoint" {
		digest, _ := specification["artifact_sha256"].(string)
		artifact, _ := specification["artifact_ref"].(string)
		if !validSHA256(digest) || artifact == "" {
			return evidence, errors.New("guarded checkpoint lacks immutable recovery evidence")
		}
		evidence["artifact_sha256"] = strings.ToLower(digest)
		evidence["artifact_ref"] = artifact
	}
	return evidence, nil
}

func validSHA256(value string) bool {
	if len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func phaseLabel(value string) string {
	parts := strings.Split(value, "_")
	for index, part := range parts {
		if part != "" {
			parts[index] = strings.ToUpper(part[:1]) + part[1:]
		}
	}
	return strings.Join(parts, "")
}

func (a *agent) nodeTransaction(ctx context.Context, manifest map[string]any) (map[string]any, error) {
	action, _ := manifest["action"].(string)
	if action != "upgrade" && action != "rollback" {
		return nil, errors.New("the outbound Node transaction action is not allowlisted")
	}
	mutationPhase := "Apply"
	if action == "rollback" {
		mutationPhase = "Rollback"
	}
	steps := []string{"Discover", "ResolveCandidate", "Preflight", "Checkpoint", mutationPhase, "RestartOrReboot", "Verify"}
	qualification, _ := manifest["qualification"].(bool)
	if qualification {
		if action != "upgrade" {
			return nil, errors.New("Node qualification requires an upgrade rollback/reapply rehearsal")
		}
		steps = append(steps, "Rollback", "Verify", "Apply", "RestartOrReboot", "Verify")
	}
	steps = append(steps, "Accept", "Cleanup")
	results := make([]map[string]any, 0, len(steps))
	mutationAttempted := false
	for _, phase := range steps {
		phaseManifest := make(map[string]any, len(manifest)+1)
		for key, value := range manifest {
			phaseManifest[key] = value
		}
		phaseManifest["phase"] = phase
		if phase == mutationPhase || (qualification && phase == "Apply") {
			mutationAttempted = true
		}
		value, err := a.nodePhase(ctx, phaseManifest)
		results = append(results, map[string]any{"phase": phase, "result": value, "passed": err == nil})
		if err != nil {
			if mutationAttempted && (phase != "Rollback" || action == "rollback") {
				safetyPhase := "Rollback"
				if action == "rollback" {
					safetyPhase = "Apply"
				}
				phaseManifest["phase"] = safetyPhase
				rollback, rollbackErr := a.nodePhase(ctx, phaseManifest)
				results = append(results, map[string]any{"phase": "AutomaticRollback", "action": safetyPhase, "result": rollback, "passed": rollbackErr == nil})
				if rollbackErr != nil {
					return map[string]any{"phases": results, "automatic_rollback": "failed"}, fmt.Errorf("%v; automatic rollback failed: %v", err, rollbackErr)
				}
				phaseManifest["phase"] = "RestartOrReboot"
				restarted, restartErr := a.nodePhase(ctx, phaseManifest)
				results = append(results, map[string]any{"phase": "AutomaticRollbackRestart", "result": restarted, "passed": restartErr == nil})
				if restartErr != nil {
					return map[string]any{"phases": results, "automatic_rollback": "failed"}, fmt.Errorf("%v; automatic rollback restart failed: %v", err, restartErr)
				}
				phaseManifest["phase"] = "Verify"
				verified, verifyErr := a.nodePhase(ctx, phaseManifest)
				results = append(results, map[string]any{"phase": "AutomaticRollbackVerify", "result": verified, "passed": verifyErr == nil})
				if verifyErr != nil {
					return map[string]any{"phases": results, "automatic_rollback": "failed"}, fmt.Errorf("%v; automatic rollback verification failed: %v", err, verifyErr)
				}
				return map[string]any{"phases": results, "automatic_rollback": "passed"}, err
			}
			return map[string]any{"phases": results}, err
		}
	}
	return map[string]any{"phases": results, "qualification_rehearsed": qualification}, nil
}

func (a *agent) nodePhase(ctx context.Context, manifest map[string]any) (map[string]any, error) {
	if err := a.verifyBundle(ctx); err != nil {
		return nil, err
	}
	phase, _ := manifest["phase"].(string)
	allowed := map[string]bool{"Discover": true, "ResolveCandidate": true, "Preflight": true, "Checkpoint": true, "Apply": true, "RestartOrReboot": true, "Verify": true, "Accept": true, "Rollback": true, "Cleanup": true}
	if !allowed[phase] {
		return nil, errors.New("Node adapter phase is invalid")
	}
	script := filepath.Join(a.config.BundleRoot, "bundle", "adapters", "node-toolchain-adapter.ps1")
	args := []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-Action", phase}
	for _, pair := range [][2]string{{"RunId", "run_id"}, {"CandidateVersion", "candidate_version"}, {"RollbackVersion", "rollback_version"}, {"ExpectedNpmVersion", "expected_npm_version"}} {
		value, _ := manifest[pair[1]].(string)
		if value == "" {
			return nil, fmt.Errorf("Node adapter manifest is missing %s", pair[1])
		}
		args = append(args, "-"+pair[0], value)
	}
	result, err := run(ctx, 35*time.Minute, "powershell.exe", args...)
	if err != nil {
		return map[string]any{"output": truncate(result, 4000)}, err
	}
	var value map[string]any
	if err := json.Unmarshal([]byte(result), &value); err != nil {
		return nil, errors.New("Node adapter returned invalid JSON")
	}
	return value, nil
}

func (a *agent) verifyBundle(ctx context.Context) error {
	allowed := filepath.Join(a.config.BundleRoot, "allowed_signers")
	manifestPath := filepath.Join(a.config.BundleRoot, "bundle-manifest.json")
	signature := filepath.Join(a.config.BundleRoot, "bundle-manifest.json.sig")
	allowedBytes, err := os.ReadFile(allowed)
	if err != nil {
		return err
	}
	trusted := sha256.Sum256(allowedBytes)
	if a.config.TrustedSignerSHA256 == "" || hex.EncodeToString(trusted[:]) != strings.ToLower(a.config.TrustedSignerSHA256) {
		return errors.New("bundle release signer does not match the installed trust pin")
	}
	manifestBytes, err := os.ReadFile(manifestPath)
	if err != nil {
		return err
	}
	verifyCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	verify := exec.CommandContext(verifyCtx, "ssh-keygen.exe", "-Y", "verify", "-f", allowed, "-I", "edsys-fleet-release", "-n", "file", "-s", signature)
	verify.Stdin = bytes.NewReader(manifestBytes)
	if output, err := verify.CombinedOutput(); err != nil {
		return fmt.Errorf("bundle signature verification failed: %s", truncate(string(output), 500))
	}
	var manifest struct {
		Files []struct {
			Path   string `json:"path"`
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		return err
	}
	for _, file := range manifest.Files {
		if file.Path == "" || filepath.IsAbs(file.Path) || strings.Contains(filepath.Clean(file.Path), "..") {
			return errors.New("bundle manifest contains an unsafe path")
		}
		content, err := os.ReadFile(filepath.Join(a.config.BundleRoot, filepath.FromSlash(file.Path)))
		if err != nil {
			return err
		}
		digest := sha256.Sum256(content)
		if hex.EncodeToString(digest[:]) != strings.ToLower(file.SHA256) {
			return fmt.Errorf("bundle file hash mismatch: %s", file.Path)
		}
	}
	return nil
}

func (a *agent) benchmark(ctx context.Context) (map[string]any, error) {
	root := filepath.Join(a.config.StateRoot, "benchmark")
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	marker := filepath.Join(root, fmt.Sprintf("authority-%d.txt", os.Getpid()))
	if err := os.WriteFile(marker, []byte("ok"), 0600); err != nil {
		return nil, err
	}
	content, err := os.ReadFile(marker)
	_ = os.Remove(marker)
	if err != nil || string(content) != "ok" {
		return nil, errors.New("outside-project file control failed")
	}
	controls := map[string]any{
		"outside_project_file": true,
		"network":              commandSuccess(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Invoke-WebRequest https://example.com -UseBasicParsing -TimeoutSec 15|Out-Null"),
		"codex_doctor":         commandSuccess(ctx, "codex.exe", "doctor", "--summary", "--ascii"),
		"docker":               commandSuccess(ctx, "docker.exe", "info"),
		"browser":              commandSuccess(ctx, `C:\Program Files\Google\Chrome\Application\chrome.exe`, "--headless=new", "--dump-dom", "https://example.com"),
	}
	for name, passed := range controls {
		if value, ok := passed.(bool); ok && !value {
			return controls, fmt.Errorf("benchmark control failed: %s", name)
		}
	}
	return controls, nil
}

func run(parent context.Context, timeout time.Duration, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	command := exec.CommandContext(ctx, name, args...)
	output, err := command.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		return string(output), errors.New("command timeout")
	}
	return strings.TrimSpace(string(output)), err
}

func commandOutput(ctx context.Context, name string, args ...string) string {
	value, err := run(ctx, 30*time.Second, name, args...)
	if err != nil {
		return "unavailable"
	}
	return truncate(value, 256)
}

func commandSuccess(ctx context.Context, name string, args ...string) bool {
	_, err := run(ctx, 60*time.Second, name, args...)
	return err == nil
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[len(value)-limit:]
}

func hostname() string {
	value, _ := os.Hostname()
	return value
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
