const { app, BrowserWindow, session, shell, Menu } = require('electron');
const path = require('path');
const fs = require('fs');

Menu.setApplicationMenu(null);

// Parse CLI flags
let targetUrl = '';
let docType = 'doc';
let initialTitle = '';
let accountId = 'default';
let email = '';
let cookiesFile = '';

for (const arg of process.argv.slice(2)) {
  if (arg.startsWith('--doc-url=')) targetUrl = arg.slice(10);
  else if (arg.startsWith('--doc-type=')) docType = arg.slice(11);
  else if (arg.startsWith('--doc-title=')) initialTitle = arg.slice(12);
  else if (arg.startsWith('--doc-account=')) accountId = arg.slice(14);
  else if (arg.startsWith('--doc-email=')) email = arg.slice(12);
  else if (arg.startsWith('--doc-cookies=')) cookiesFile = arg.slice(14);
}

const typeMap = {
  sheet: { wm: 'google-sheets', name: 'Google Planilhas', icon: 'sheets.png' },
  spreadsheet: { wm: 'google-sheets', name: 'Google Planilhas', icon: 'sheets.png' },
  doc: { wm: 'google-docs', name: 'Google Docs', icon: 'docs.png' },
  document: { wm: 'google-docs', name: 'Google Docs', icon: 'docs.png' },
  slide: { wm: 'google-slides', name: 'Google Apresentações', icon: 'slides.png' },
  presentation: { wm: 'google-slides', name: 'Google Apresentações', icon: 'slides.png' },
  form: { wm: 'google-forms', name: 'Google Formulários', icon: 'drive_fs.png' }
};

const meta = typeMap[docType] || typeMap.doc;
app.setName(meta.name);
if (process.platform === 'linux') {
  app.setDesktopName(meta.wm + '.desktop');
}

// Icon path fallback
let iconPath = path.resolve(__dirname, '../../assets/icons', meta.icon);
if (!fs.existsSync(iconPath)) {
  iconPath = path.resolve('/opt/google-drive-linux/assets/icons', meta.icon);
}

async function initSession(ses) {
  ses.setUserAgent(
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
  );

  // If a cookies seed file was provided and partition cookies are empty, seed them
  if (cookiesFile && fs.existsSync(cookiesFile)) {
    try {
      const existing = await ses.cookies.get({ domain: 'google.com' });
      if (existing.length === 0) {
        const raw = fs.readFileSync(cookiesFile, 'utf-8');
        const list = JSON.parse(raw);
        for (const c of list) {
          try {
            let domainClean = c.domain || '.google.com';
            if (domainClean.startsWith('.')) domainClean = domainClean.slice(1);
            const pathClean = c.path || '/';
            const protocol = c.secure ? 'https://' : 'http://';
            const url = `${protocol}${domainClean}${pathClean}`;

            const cookieObj = {
              url: url,
              name: c.name,
              value: c.value
            };
            if (c.domain) cookieObj.domain = c.domain;
            if (c.path) cookieObj.path = c.path;
            if (typeof c.secure === 'boolean') cookieObj.secure = c.secure;
            if (typeof c.httpOnly === 'boolean') cookieObj.httpOnly = c.httpOnly;
            if (typeof c.expirationDate === 'number' && c.expirationDate > 0) {
              cookieObj.expirationDate = c.expirationDate;
            }

            await ses.cookies.set(cookieObj).catch(() => {});
          } catch (_) {}
        }
      }
    } catch (e) {
      console.error('Error seeding cookies:', e);
    }
  }
}

async function createWindow() {
  const safeAcc = accountId ? accountId.replace(/[^a-zA-Z0-9_-]/g, '_') : 'default';
  const ses = session.fromPartition('persist:google_account_' + safeAcc, { cache: true });

  await initSession(ses);

  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    title: initialTitle ? `${initialTitle} - ${meta.name}` : meta.name,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    autoHideMenuBar: true,
    webPreferences: {
      session: ses,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      spellcheck: true
    }
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.includes('docs.google.com') || url.includes('accounts.google.com') || url.includes('drive.google.com')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  win.on('page-title-updated', (event, title) => {
    event.preventDefault();
    let clean = title
      .replace(/ - Google (Sheets|Docs|Slides|Planilhas|Apresentações)$/, '')
      .replace(/ - Google Planilhas$/, '')
      .replace(/ - Google Docs$/, '')
      .replace(/ - Google Apresentações$/, '')
      .trim();
    win.setTitle(clean ? `${clean} - ${meta.name}` : meta.name);
  });

  // Keyboard navigation
  win.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    if (input.key === 'F11') {
      win.setFullScreen(!win.isFullScreen());
      event.preventDefault();
    } else if (input.key === 'F5' || (input.control && input.key.toLowerCase() === 'r')) {
      win.webContents.reload();
      event.preventDefault();
    } else if (input.control && (input.key.toLowerCase() === 'w' || input.key.toLowerCase() === 'q')) {
      win.close();
      event.preventDefault();
    }
  });

  if (targetUrl) {
    win.loadURL(targetUrl);
  } else {
    const homes = {
      sheet: 'https://docs.google.com/spreadsheets/u/0/',
      spreadsheet: 'https://docs.google.com/spreadsheets/u/0/',
      doc: 'https://docs.google.com/document/u/0/',
      document: 'https://docs.google.com/document/u/0/',
      slide: 'https://docs.google.com/presentation/u/0/',
      presentation: 'https://docs.google.com/presentation/u/0/'
    };
    win.loadURL(homes[docType] || homes.doc);
  }
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
