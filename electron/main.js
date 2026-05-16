'use strict';

const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const FLASK_PORT = 5050;
const FLASK_URL = `http://localhost:${FLASK_PORT}`;

let flaskProcess = null;
let mainWindow = null;

// ---------------------------------------------------------------------------
// Flask
// ---------------------------------------------------------------------------

function getAppRoot() {
  // In production the app root is the directory containing the .app bundle's
  // Resources folder; in development it's the repo root.
  if (app.isPackaged) {
    return path.join(process.resourcesPath, '..');
  }
  return path.join(__dirname, '..');
}

function getPythonPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'venv', 'bin', 'python');
  }
  return path.join(__dirname, '..', 'venv', 'bin', 'python');
}

function startFlask() {
  return new Promise((resolve, reject) => {
    const python = path.join(__dirname, '..', 'venv', 'bin', 'python');
    const script = path.join(__dirname, '..', 'app.py');
    const cwd = path.join(__dirname, '..');

    const env = Object.assign({}, process.env, { LEDGER_ENV: 'production' });

    flaskProcess = spawn(python, [script], { cwd, env });

    flaskProcess.stdout.on('data', data => {
      console.log('[Flask]', data.toString().trim());
    });

    flaskProcess.stderr.on('data', data => {
      console.error('[Flask err]', data.toString().trim());
    });

    flaskProcess.on('error', err => {
      reject(new Error(`Failed to spawn Flask: ${err.message}`));
    });

    flaskProcess.on('exit', (code, signal) => {
      if (code !== 0 && code !== null) {
        console.error(`Flask exited with code ${code}`);
      }
    });

    // Poll until Flask is ready
    const start = Date.now();
    let attempt = 0;
    const interval = setInterval(() => {
      attempt += 1;
      console.log(`Waiting for Flask... attempt ${attempt}`);
      http.get(FLASK_URL, res => {
        clearInterval(interval);
        resolve();
      }).on('error', () => {
        if (Date.now() - start > 60000) {
          clearInterval(interval);
          reject(new Error('Flask did not start within 60 seconds.'));
        }
      });
    }, 1000);
  });
}

function stopFlask() {
  if (flaskProcess) {
    flaskProcess.kill('SIGTERM');
    flaskProcess = null;
  }
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'Ledger — Joe Davis Arts & Media',
    webPreferences: {
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.loadURL(FLASK_URL);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  try {
    await startFlask();
    createWindow();
  } catch (err) {
    dialog.showErrorBox('Ledger — Startup Error', err.message);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  stopFlask();
  app.quit();
});

app.on('before-quit', () => {
  stopFlask();
});
