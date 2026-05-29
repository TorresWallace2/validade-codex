
const STORAGE_KEY = 'docmgr-login';

function loadSavedCredentials() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw);
  } catch (error) {
    console.error('Failed to parse saved credentials', error);
    return null;
  }
}

function saveCredentials(data) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (error) {
    console.error('Failed to save credentials', error);
  }
}

function clearCredentials() {
  localStorage.removeItem(STORAGE_KEY);
}

function setInfo(message, type = 'muted') {
  const info = document.getElementById('loginInfo');
  if (!info) {
    return;
  }
  info.textContent = message;
  info.className = `text-center small text-${type}`;
}

async function checkExistingSession() {
  try {
    const response = await fetch('/api/auth/session');
    if (response.ok) {
      const payload = await response.json();
      if (payload.success && payload.data) {
        window.location.href = '/';
      }
    }
  } catch (error) {
    console.error(error);
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const usernameInput = document.getElementById('username');
  const passwordInput = document.getElementById('password');
  const rememberCheckbox = document.getElementById('rememberMe');
  const button = document.getElementById('btnLogin');

  if (!usernameInput || !passwordInput || !button) {
    return;
  }

  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    setInfo('Informe usuario e senha.', 'danger');
    return;
  }

  button.disabled = true;
  setInfo('Validando credenciais...', 'muted');

  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, remember: rememberCheckbox?.checked }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Nao foi possivel autenticar.');
    }
    if (rememberCheckbox?.checked) {
      saveCredentials({ username, password, remember: true });
    } else {
      clearCredentials();
    }
    window.location.href = '/';
  } catch (error) {
    console.error(error);
    setInfo(error.message || 'Nao foi possivel autenticar.', 'danger');
    button.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  checkExistingSession();
  const stored = loadSavedCredentials();
  const usernameInput = document.getElementById('username');
  const passwordInput = document.getElementById('password');
  const rememberCheckbox = document.getElementById('rememberMe');

  if (stored && usernameInput && passwordInput) {
    usernameInput.value = stored.username || '';
    passwordInput.value = stored.password || '';
    if (rememberCheckbox) {
      rememberCheckbox.checked = Boolean(stored.remember);
    }
    if (stored.username) {
      usernameInput.focus();
      usernameInput.setSelectionRange(stored.username.length, stored.username.length);
    }
  }

  const form = document.getElementById('loginForm');
  if (form) {
    form.addEventListener('submit', handleLogin);
  }
});
