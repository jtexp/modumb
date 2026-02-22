# Jenkins Setup Guide for Modumb (Windows)

## Context

The Jenkinsfile and `test_e2e_vac.py` changes are already committed on the `worktree-cicd` branch. This guide walks through installing Jenkins on Windows and configuring it to run the Modumb pipeline.

The critical constraint: E2E tests use Virtual Audio Cable hardware, which requires an **interactive Windows session** (not Session 0 where services run). Jenkins must be launched as a regular application so it can access audio devices.

---

## Step 1: Install Java (prerequisite)

Jenkins requires Java 17+.

1. Download **Eclipse Temurin JDK 21** (LTS) from https://adoptium.net/
   - Choose: Windows x64, `.msi` installer
2. Run the installer — accept defaults
3. Verify in a **new** Command Prompt:
   ```
   java -version
   ```
   Should show `openjdk version "21.x.x"`.

---

## Step 2: Download Jenkins

1. Download the **Generic Java package (.war)** from https://www.jenkins.io/download/
   - Direct link: the "Generic Java package (.war)" option under LTS
   - Save to `C:\Jenkins\jenkins.war`
2. Create the folder if needed: `mkdir C:\Jenkins`

> We use the WAR file (not the MSI installer) because the MSI installs Jenkins as a Windows service, which runs in Session 0 with no audio device access. The WAR file lets us run Jenkins in our desktop session.

---

## Step 3: First launch

Open Command Prompt (not PowerShell — avoids quoting issues) and run:

```
java -jar C:\Jenkins\jenkins.war --httpPort=8090
```

> Port 8090 avoids conflict with Modumb's proxy on 8080.

On first launch:
1. Wait for `Jenkins is fully up and running` in the console
2. Open http://localhost:8090 in a browser
3. It shows an "Unlock Jenkins" page with a path to the initial admin password
   - The path is printed in the console output, something like:
     `C:\Users\John\.jenkins\secrets\initialAdminPassword`
   - Copy the password from that file and paste it in the browser
4. Choose **Install suggested plugins** — this installs Git, Pipeline, and other basics
5. Create an admin user (or skip to use the initial admin)
6. Accept the default Jenkins URL: `http://localhost:8090/`
7. Click "Start using Jenkins"

Press Ctrl+C in the console to stop Jenkins when done configuring.

---

## Step 4: Install additional plugins

1. Go to **Manage Jenkins > Plugins > Available plugins**
2. Search for and install these (check the box, click "Install"):
   - **Lockable Resources** — for `lock('vac-audio-devices')` in the pipeline
   - **GitHub** — for webhook integration (optional, for auto-triggering on push)
   - **JUnit** — for test result publishing (may already be installed via suggested plugins)
3. Restart Jenkins when prompted (or via **Manage Jenkins > Restart**)

> Pipeline, Git, and Workspace Cleanup plugins should already be installed from the "suggested plugins" step. Verify under Manage Jenkins > Plugins > Installed.

---

## Step 5: Create the lockable resource

1. Go to **Manage Jenkins > Lockable Resources** (under System Configuration)
   - If you don't see it, the plugin isn't installed — go back to Step 4
2. Click **Add Lockable Resource**
3. Set:
   - **Name**: `vac-audio-devices`
   - **Description**: `Virtual Audio Cable devices — only one E2E test at a time`
   - Leave Labels and other fields empty
4. Click **Save**

This is the lock the Jenkinsfile acquires with `lock('vac-audio-devices')` to prevent concurrent E2E tests from fighting over the same audio hardware.

---

## Step 6: Label the built-in node

The Jenkinsfile uses `agent { label 'windows-audio' }`. Since we're running Jenkins as a desktop app (not a service), the built-in node already has audio access — we just need to label it.

1. Go to **Manage Jenkins > Nodes** (under System Configuration)
2. Click on the **Built-In Node** (or "master")
3. Click **Configure** (gear icon)
4. Set:
   - **Labels**: `windows-audio`
   - **Usage**: "Only build jobs with label expressions matching this node"
5. Click **Save**

---

## Step 7: Add GitHub credentials

1. Go to **Manage Jenkins > Credentials > System > Global credentials**
2. Click **Add Credentials**
3. Set:
   - **Kind**: Username with password
   - **Username**: your GitHub username
   - **Password**: a GitHub Personal Access Token (PAT) with `repo` scope
     - Create one at https://github.com/settings/tokens if you don't have one
   - **ID**: `github-pat` (or any memorable ID)
   - **Description**: `GitHub PAT for modumb`
4. Click **Create**

---

## Step 8: Create the Multibranch Pipeline job

1. From the Jenkins dashboard, click **New Item**
2. Enter name: `modumb`
3. Select **Multibranch Pipeline**
4. Click **OK**
5. Under **Branch Sources**, click **Add source > GitHub**
   - **Credentials**: select the credential from Step 7
   - **Repository HTTPS URL**: `https://github.com/jtexp/modumb.git`
6. Under **Build Configuration**:
   - **Mode**: "by Jenkinsfile" (default)
   - **Script Path**: `Jenkinsfile` (default)
7. Under **Scan Multibranch Pipeline Triggers** (optional):
   - Check "Periodically if not otherwise run"
   - Set interval: 5 minutes (fallback polling if webhook isn't set up)
8. Click **Save**

Jenkins will immediately scan branches. Any branch with a `Jenkinsfile` will trigger a build.

---

## Step 9: Verify the first build

After saving the job in Step 8:

1. Jenkins scans and finds the branch with the Jenkinsfile
2. A build starts automatically — click into it to watch the console
3. **Unit Tests** stage should pass
4. **Check E2E Needed** stage should report `E2E needed: false` (no modem code changes vs master)
5. **E2E Smoke Tests** stage should be skipped
6. Build result: **SUCCESS**

If something fails, check the console output — common issues:
- Python path wrong: verify `C:\Users\John\modumb\.venv\Scripts\python.exe` exists
- Git not found: install Git for Windows if not already installed
- Pytest not found: run `C:\Users\John\modumb\.venv\Scripts\python.exe -m pip install -e ".[dev]"` from the repo root

---

## Step 10: Test E2E manually

To verify E2E tests work through Jenkins:

1. Navigate to the branch build page
2. Click **Build with Parameters**
3. Check **RUN_FULL_MATRIX** = `true`, then click **Build**
   - This forces E2E stages to run even when no modem source files changed
4. Watch the console — it should acquire the `vac-audio-devices` lock and run smoke tests
5. Verify all 4 smoke tests pass

---

## Step 11: Auto-start Jenkins on login (optional)

So Jenkins survives reboots without manual startup:

1. Press `Win+R`, type `shell:startup`, press Enter — opens the Startup folder
2. Create a shortcut:
   - Right-click > New > Shortcut
   - Target: `javaw -jar C:\Jenkins\jenkins.war --httpPort=8090`
   - Name: `Jenkins`
3. `javaw` (not `java`) runs without a console window

> Alternatively, create a `.bat` file in the Startup folder:
> ```
> start /min java -jar C:\Jenkins\jenkins.war --httpPort=8090
> ```

---

## Step 12: GitHub webhook (optional)

If you want pushes to automatically trigger builds (instead of polling):

1. In Jenkins, go to **Manage Jenkins > System** (Configure System)
2. Scroll to **GitHub** section, click **Add GitHub Server**
   - **API URL**: `https://api.github.com` (default)
   - **Credentials**: add a "Secret text" credential with your GitHub PAT
   - Check **Manage hooks**
3. Click **Save**
4. On GitHub, go to **repo Settings > Webhooks > Add webhook**:
   - **Payload URL**: `http://<your-ip>:8090/github-webhook/`
   - **Content type**: `application/json`
   - **Events**: "Just the push event"

> This requires your machine to be reachable from the internet. For local-only dev, the polling trigger from Step 8 is sufficient.

---

## Quick Reference

| What | Where |
|------|-------|
| Jenkins home | `C:\Users\John\.jenkins\` (default) |
| Jenkins WAR | `C:\Jenkins\jenkins.war` |
| Jenkins URL | http://localhost:8090 |
| Pipeline job | `modumb` (Multibranch Pipeline) |
| Lock resource | `vac-audio-devices` |
| Node label | `windows-audio` (on Built-In Node) |
| Repo | `https://github.com/jtexp/modumb.git` |

## Verification checklist

- [ ] Jenkins running at http://localhost:8090
- [ ] Lockable Resources plugin installed, `vac-audio-devices` resource created
- [ ] Built-In Node labeled `windows-audio`
- [ ] `modumb` Multibranch Pipeline job created, scanning branches
- [ ] Branch with Jenkinsfile detected and unit tests pass
- [ ] `RUN_FULL_MATRIX=true` build acquires lock and runs E2E smoke tests
- [ ] Two simultaneous builds: second queues waiting for the lock
