'use strict';

const { app, BrowserWindow, ipcMain, net } = require('electron');
const path = require('path');

const APP_URL = 'https://ledger-jdam.onrender.com';

let mainWindow = null;

function loadApp() {
  if (net.isOnline()) {
    mainWindow.loadURL(APP_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, 'offline.html'));
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'Quilk — Joe Davis Arts & Media',
    webPreferences: {
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.webContents.on('did-fail-load', (_event, errorCode) => {
    // -3 is ERR_ABORTED (e.g. a redirect cancelled the load) — ignore it
    if (errorCode !== -3) {
      mainWindow.loadFile(path.join(__dirname, 'offline.html'));
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  loadApp();
}

ipcMain.on('retry', () => {
  if (mainWindow) loadApp();
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});
