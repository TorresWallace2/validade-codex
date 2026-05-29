const originalFetch = window.fetch;
window.fetch = async (...args) => {
  const response = await originalFetch(...args);
  if (response.status === 401) {
    window.location.href = '/auth/login';
  }
  return response;
};

const state = {
  currentPath: '',
  sortBy: 'name',
  direction: 'asc',
  page: 1,
  pageSize: 50,
  hasMore: true,
  isLoading: false,
  search: '',
  statusFilter: [],
  selectedPath: null,
  selectedType: null,
  detail: null,
  notesSnapshot: '',
  globalWarningDays: 15,
  autoRefreshTimer: null,
  pregoes: [],
  favorites: [],
  parentPath: '',
  selectedPregao: null,
  selectedFavorite: null,
  selectedPaths: new Set(),
  transferMode: null,
  transferTargetPath: '',
  transferParentPath: '',
  currentUser: null,
  userList: [],
  googleDriveConnected: false,
  googleDriveRootPath: 'gdrive://root',
};

const elements = {};
const modals = {};
let sentinelObserver = null;

function cacheElements() {
  elements.tableBody = document.getElementById('itemsBody');
  elements.userBanner = document.getElementById('userBanner');
  elements.currentUserName = document.getElementById('currentUserName');
  elements.currentUserRole = document.getElementById('currentUserRole');
  elements.btnLogout = document.getElementById('btnLogout');
  elements.btnManageUsers = document.getElementById('btnManageUsers');
  elements.userManagerModal = document.getElementById('userManagerModal');
  elements.userListBody = document.getElementById('userListBody');
  elements.userCreateForm = document.getElementById('userCreateForm');
  elements.userCreateUsername = document.getElementById('userCreateUsername');
  elements.userCreatePassword = document.getElementById('userCreatePassword');
  elements.userCreateRole = document.getElementById('userCreateRole');
  elements.itemsSummary = document.getElementById('itemsSummary');
  elements.loadingOverlay = document.getElementById('loadingOverlay');
  elements.breadcrumb = document.getElementById('breadcrumb');
  elements.addressInput = document.getElementById('addressInput');
  elements.btnGoAddress = document.getElementById('btnGoAddress');
  elements.btnNavigateUp = document.getElementById('btnNavigateUp');
  elements.darkModeToggle = document.getElementById('darkModeToggle');
  elements.btnGoogleDrive = document.getElementById('btnGoogleDrive');
  elements.btnDisconnectGoogleDrive = document.getElementById('btnDisconnectGoogleDrive');
  elements.searchInput = document.getElementById('searchInput');
  elements.btnClearSearch = document.getElementById('btnClearSearch');
  elements.btnApplyStatus = document.getElementById('btnApplyStatus');
  elements.statusFilter = document.getElementById('statusFilter');
  elements.btnRefresh = document.getElementById('btnRefresh');
  elements.btnExport = document.getElementById('btnExport');
  elements.btnMove = document.getElementById('btnMove');
  elements.btnCopy = document.getElementById('btnCopy');
  elements.btnUpload = document.getElementById('btnUpload');
  elements.btnNewFolder = document.getElementById('btnNewFolder');
  elements.btnNewFile = document.getElementById('btnNewFile');
  elements.selectAllRows = document.getElementById('selectAllRows');
  elements.btnAddPregao = document.getElementById('btnAddPregao');
  elements.pregaoList = document.getElementById('pregaoList');
  elements.pregaoDropdown = document.getElementById('pregaoDropdown');
  elements.btnAddFavorite = document.getElementById('btnAddFavorite');
  elements.favoriteList = document.getElementById('favoriteList');
  elements.favoriteDropdown = document.getElementById('favoriteDropdown');
  elements.detailName = document.getElementById('detailName');
  elements.detailPath = document.getElementById('detailPath');
  elements.detailSize = document.getElementById('detailSize');
  elements.detailModified = document.getElementById('detailModified');
  elements.detailValidity = document.getElementById('detailValidity');
  elements.detailStatus = document.getElementById('detailStatus');
  elements.detailWarningDays = document.getElementById('detailWarningDays');
  elements.notesInput = document.getElementById('notesInput');
  elements.btnSaveNotes = document.getElementById('btnSaveNotes');
  elements.btnResetNotes = document.getElementById('btnResetNotes');
  elements.btnOpenFile = document.getElementById('btnOpenFile');
  elements.btnOpenFolder = document.getElementById('btnOpenFolder');
  elements.btnSetValidity = document.getElementById('btnSetValidity');
  elements.btnMarkIndeterminate = document.getElementById('btnMarkIndeterminate');
  elements.btnClearValidity = document.getElementById('btnClearValidity');
  elements.btnRename = document.getElementById('btnRename');
  elements.btnDelete = document.getElementById('btnDelete');
  elements.warningSettings = document.getElementById('btnWarningSettings');
  elements.validityDate = document.getElementById('validityDate');
  elements.warningDays = document.getElementById('warningDays');
  elements.validityForm = document.getElementById('validityForm');
  elements.btnConfirmValidity = document.getElementById('btnConfirmValidity');
  elements.renameInput = document.getElementById('renameInput');
  elements.btnConfirmRename = document.getElementById('btnConfirmRename');
  elements.newFolderName = document.getElementById('newFolderName');
  elements.btnCreateFolder = document.getElementById('btnCreateFolder');
  elements.newFileName = document.getElementById('newFileName');
  elements.btnCreateFile = document.getElementById('btnCreateFile');
  elements.uploadInput = document.getElementById('uploadInput');
  elements.btnConfirmUpload = document.getElementById('btnConfirmUpload');
  elements.globalWarningDays = document.getElementById('globalWarningDays');
  elements.btnSaveWarningDays = document.getElementById('btnSaveWarningDays');
  elements.pregaoName = document.getElementById('pregaoName');
  elements.pregaoPath = document.getElementById('pregaoPath');
  elements.btnSavePregao = document.getElementById('btnSavePregao');
  elements.favoriteName = document.getElementById('favoriteName');
  elements.favoritePath = document.getElementById('favoritePath');
  elements.btnSaveFavorite = document.getElementById('btnSaveFavorite');
  elements.transferPathInput = document.getElementById('transferPathInput');
  elements.btnTransferGo = document.getElementById('btnTransferGo');
  elements.btnTransferUp = document.getElementById('btnTransferUp');
  elements.transferDirectoryList = document.getElementById('transferDirectoryList');
  elements.btnTransferCreateFolder = document.getElementById('btnTransferCreateFolder');
  elements.btnConfirmTransfer = document.getElementById('btnConfirmTransfer');
  elements.transferSelectionSummary = document.getElementById('transferSelectionSummary');
  elements.transferModalTitle = document.getElementById('transferModalTitle');
}

function setupModals() {
  modals.validity = new bootstrap.Modal(document.getElementById('validityModal'));
  modals.rename = new bootstrap.Modal(document.getElementById('renameModal'));
  modals.createFolder = new bootstrap.Modal(document.getElementById('createFolderModal'));
  modals.createFile = new bootstrap.Modal(document.getElementById('createFileModal'));
  modals.upload = new bootstrap.Modal(document.getElementById('uploadModal'));
  modals.warningDays = new bootstrap.Modal(document.getElementById('warningDaysModal'));
  modals.pregao = new bootstrap.Modal(document.getElementById('pregaoModal'));
  modals.favorite = new bootstrap.Modal(document.getElementById('favoriteModal'));
  modals.userManager = new bootstrap.Modal(document.getElementById('userManagerModal'));
  modals.transfer = new bootstrap.Modal(document.getElementById('transferModal'));

  document.getElementById('validityModal').addEventListener('show.bs.modal', () => {
    if (!state.detail) {
      return;
    }
    const type = state.detail.validity_type || 'not_defined';
    const dateInput = elements.validityDate;
    const warningInput = elements.warningDays;
    elements.validityForm.querySelectorAll('input[name="validityType"]').forEach((radio) => {
      radio.checked = radio.value === type;
    });
    if (state.detail.validity_type === 'defined') {
      dateInput.value = state.detail.validity;
    } else {
      dateInput.value = '';
    }
    warningInput.value = state.detail.warning_days ?? state.globalWarningDays;
    toggleValidityDateGroup();
  });

  const pregaoModalEl = document.getElementById('pregaoModal');
  if (pregaoModalEl) {
    pregaoModalEl.addEventListener('show.bs.modal', () => {
      if (elements.pregaoName) elements.pregaoName.value = '';
      if (elements.pregaoPath) elements.pregaoPath.value = state.currentPath || '';
    });
  }

  const favoriteModalEl = document.getElementById('favoriteModal');
  if (favoriteModalEl) {
    favoriteModalEl.addEventListener('show.bs.modal', () => {
      if (elements.favoriteName) elements.favoriteName.value = '';
      if (elements.favoritePath) elements.favoritePath.value = state.currentPath || '';
    });
  }

  const transferModalEl = document.getElementById('transferModal');
  if (transferModalEl) {
    transferModalEl.addEventListener('hidden.bs.modal', () => {
      state.transferMode = null;
      state.transferTargetPath = '';
      state.transferParentPath = '';
      if (elements.transferSelectionSummary) {
        elements.transferSelectionSummary.textContent = '';
      }
      if (elements.transferDirectoryList) {
        elements.transferDirectoryList.innerHTML = '';
      }
    });
  }

  document.getElementById('validityModal').addEventListener('hidden.bs.modal', () => {
    elements.validityDate.value = '';
  });

  document.getElementById('renameModal').addEventListener('show.bs.modal', () => {
    if (state.detail) {
      elements.renameInput.value = state.detail.name;
      elements.renameInput.focus();
      elements.renameInput.select();
    }
  });

  document.getElementById('createFolderModal').addEventListener('shown.bs.modal', () => {
    elements.newFolderName.value = '';
    elements.newFolderName.focus();
  });

  document.getElementById('createFileModal').addEventListener('shown.bs.modal', () => {
    elements.newFileName.value = '';
    elements.newFileName.focus();
  });

  document.getElementById('pregaoModal').addEventListener('shown.bs.modal', () => {
    if (elements.pregaoName) {
      elements.pregaoName.value = '';
      elements.pregaoName.focus();
    }
  });

  document.getElementById('favoriteModal').addEventListener('shown.bs.modal', () => {
    if (elements.favoriteName) {
      elements.favoriteName.value = '';
      elements.favoriteName.focus();
    }
  });
}

function setupListeners() {
  elements.darkModeToggle.addEventListener('change', handleThemeToggle);
  if (elements.btnLogout) {
    elements.btnLogout.addEventListener('click', submitLogout);
  }
  if (elements.btnManageUsers) {
    elements.btnManageUsers.addEventListener('click', openUserManager);
  }
  if (elements.btnGoogleDrive) {
    elements.btnGoogleDrive.addEventListener('click', handleGoogleDriveButton);
  }
  if (elements.btnDisconnectGoogleDrive) {
    elements.btnDisconnectGoogleDrive.addEventListener('click', disconnectGoogleDrive);
  }
  if (elements.userCreateForm) {
    elements.userCreateForm.addEventListener('submit', handleUserCreate);
  }
  if (elements.userListBody) {
    elements.userListBody.addEventListener('click', handleUserAction);
  }
  elements.btnRefresh.addEventListener('click', () => reloadDirectory(true));
  elements.btnExport.addEventListener('click', handleExport);
  if (elements.btnNavigateUp) {
    elements.btnNavigateUp.addEventListener('click', navigateUpDirectory);
  }
  if (elements.btnMove) {
    elements.btnMove.addEventListener('click', () => openTransferModal('move'));
  }
  if (elements.btnCopy) {
    elements.btnCopy.addEventListener('click', () => openTransferModal('copy'));
  }
  elements.btnClearSearch.addEventListener('click', () => {
    elements.searchInput.value = '';
    state.search = '';
    reloadDirectory(true);
  });
  elements.btnApplyStatus.addEventListener('click', applyStatusFilter);
  elements.btnSaveNotes.addEventListener('click', saveNotes);
  elements.btnResetNotes.addEventListener('click', resetNotes);
  elements.btnOpenFile.addEventListener('click', () => triggerSimpleAction('/api/open_file'));
  elements.btnOpenFolder.addEventListener('click', () => triggerSimpleAction('/api/open_folder'));
  elements.btnMarkIndeterminate.addEventListener('click', () => quickValidity('indeterminate'));
  elements.btnClearValidity.addEventListener('click', () => quickValidity('not_defined'));
  elements.btnConfirmValidity.addEventListener('click', submitValidity);
  elements.btnConfirmRename.addEventListener('click', submitRename);
  elements.btnCreateFolder.addEventListener('click', submitCreateFolder);
  elements.btnCreateFile.addEventListener('click', submitCreateFile);
  elements.btnConfirmUpload.addEventListener('click', submitUpload);
  elements.btnDelete.addEventListener('click', submitDelete);
  elements.btnSaveWarningDays.addEventListener('click', submitWarningDays);
  elements.btnAddPregao.addEventListener('click', openPregaoModal);
  elements.btnSavePregao.addEventListener('click', submitPregao);
  elements.btnAddFavorite.addEventListener('click', openFavoriteModal);
  elements.btnSaveFavorite.addEventListener('click', submitFavorite);
  if (elements.selectAllRows) {
    elements.selectAllRows.addEventListener('change', handleSelectAllChange);
  }
  if (elements.btnTransferGo) {
    elements.btnTransferGo.addEventListener('click', () => navigateTransferPath());
  }
  if (elements.transferPathInput) {
    elements.transferPathInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        navigateTransferPath();
      }
    });
  }
  if (elements.btnTransferUp) {
    elements.btnTransferUp.addEventListener('click', () => navigateTransferParent());
  }
  if (elements.btnTransferCreateFolder) {
    elements.btnTransferCreateFolder.addEventListener('click', createFolderInTransfer);
  }
  if (elements.btnConfirmTransfer) {
    elements.btnConfirmTransfer.addEventListener('click', submitTransferAction);
  }
  elements.warningSettings.addEventListener('click', openWarningSettings);

  let searchDebounce = null;
  elements.searchInput.addEventListener('input', (event) => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      state.search = event.target.value.trim();
      reloadDirectory(true);
    }, 300);
  });

  elements.validityForm.querySelectorAll('input[name="validityType"]').forEach((radio) => {
    radio.addEventListener('change', toggleValidityDateGroup);
  });

  elements.validityDate.addEventListener('input', maskDateInput);

  document.querySelectorAll('#itemsTable thead th.sortable').forEach((header) => {
    header.addEventListener('click', () => {
      const field = header.dataset.sort;
      if (!field) {
        return;
      }
      if (state.sortBy === field) {
        state.direction = state.direction === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortBy = field;
        state.direction = 'asc';
      }
      updateSortIndicators();
      reloadDirectory(true);
    });
  });

  updateSelectionIndicators();
}

function initSentinel() {
  if (sentinelObserver) {
    sentinelObserver.disconnect();
  }
  sentinelObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting && state.hasMore && !state.isLoading) {
        loadDirectory(false);
      }
    });
  }, {
    root: document.querySelector('#itemsTable').parentElement,
    threshold: 0.2,
  });
}

function attachSentinel() {
  let sentinelRow = document.getElementById('sentinelRow');
  if (!sentinelRow) {
    sentinelRow = document.createElement('tr');
    sentinelRow.id = 'sentinelRow';
    const td = document.createElement('td');
    td.colSpan = 6;
    td.className = 'text-center text-muted small';
    td.textContent = 'Carregando mais itens...';
    sentinelRow.appendChild(td);
    elements.tableBody.appendChild(sentinelRow);
  }
  if (sentinelObserver) {
    sentinelObserver.observe(sentinelRow);
  }
  sentinelRow.classList.toggle('d-none', !state.hasMore);
}

async function bootstrapApp() {
  cacheElements();
  await fetchSession();
  await fetchGoogleDriveStatus();
  updateUserBanner();
  updateGoogleDriveButtons();
  updatePregaoSelectionButton();
  updateFavoriteSelectionButton();
  setupModals();
  setupListeners();
  initSentinel();
  restorePreferences();
  await fetchWarningDays();
  await fetchPregoes();
  await fetchFavorites();
  await reloadDirectory(true, { silent: false });
  startAutoRefresh();
}

document.addEventListener('DOMContentLoaded', bootstrapApp);

function restorePreferences() {
  const theme = localStorage.getItem('docmgr-theme') || 'light';
  document.documentElement.setAttribute('data-bs-theme', theme);
  elements.darkModeToggle.checked = theme === 'dark';

  const params = new URLSearchParams(window.location.search);
  if (params.get('drive') === 'connected') {
    state.currentPath = state.googleDriveRootPath;
    localStorage.setItem('docmgr-last-path', state.currentPath);
    window.history.replaceState({}, document.title, window.location.pathname);
    return;
  }
  const lastPath = localStorage.getItem('docmgr-last-path');
  if (lastPath) {
    state.currentPath = lastPath;
  }
}

function handleThemeToggle(event) {
  const enabled = event.target.checked;
  const theme = enabled ? 'dark' : 'light';
  document.documentElement.setAttribute('data-bs-theme', theme);
  localStorage.setItem('docmgr-theme', theme);
}

function startAutoRefresh() {
  if (state.autoRefreshTimer) {
    clearInterval(state.autoRefreshTimer);
  }
  state.autoRefreshTimer = setInterval(() => {
    reloadDirectory(true, { silent: true, preserveSelection: true });
  }, 60000);
}

function setLoading(isLoading) {
  state.isLoading = isLoading;
  if (!elements.loadingOverlay) {
    return;
  }
  elements.loadingOverlay.classList.toggle('d-none', !isLoading);
}

function maskDateInput(event) {
  const value = event.target.value.replace(/\D/g, '');
  let masked = value;
  if (value.length >= 3 && value.length <= 4) {
    masked = `${value.slice(0, 2)}/${value.slice(2)}`;
  } else if (value.length > 4) {
    masked = `${value.slice(0, 2)}/${value.slice(2, 4)}/${value.slice(4, 8)}`;
  }
  event.target.value = masked;
}

function toggleValidityDateGroup() {
  const selected = elements.validityForm.querySelector('input[name="validityType"]:checked');
  const group = document.getElementById('validityDateGroup');
  if (!selected || !group) {
    return;
  }
  const show = selected.value === 'defined';
  group.classList.toggle('d-none', !show);
}




function isGoogleDrivePath(path = state.currentPath) {
  return Boolean(path && path.startsWith('gdrive://'));
}

async function fetchGoogleDriveStatus() {
  try {
    const response = await fetch('/api/google-drive/status');
    const payload = await response.json();
    if (payload.success && payload.data) {
      state.googleDriveConnected = Boolean(payload.data.connected);
      state.googleDriveRootPath = payload.data.root_path || 'gdrive://root';
    }
  } catch (error) {
    console.error(error);
  }
}

function updateGoogleDriveButtons() {
  if (elements.btnGoogleDrive) {
    elements.btnGoogleDrive.innerHTML = state.googleDriveConnected
      ? '<i class="bi bi-google me-1"></i>Abrir Google Drive'
      : '<i class="bi bi-google me-1"></i>Conectar Google Drive';
  }
  if (elements.btnDisconnectGoogleDrive) {
    elements.btnDisconnectGoogleDrive.classList.toggle('d-none', !state.googleDriveConnected);
  }
}

function handleGoogleDriveButton() {
  if (!state.googleDriveConnected) {
    window.location.href = '/auth/google/connect';
    return;
  }
  state.currentPath = state.googleDriveRootPath;
  if (elements.addressInput) {
    elements.addressInput.value = state.currentPath;
  }
  reloadDirectory(true);
}

async function disconnectGoogleDrive() {
  try {
    await fetch('/api/google-drive/disconnect', { method: 'POST' });
  } catch (error) {
    console.error(error);
  }
  state.googleDriveConnected = false;
  updateGoogleDriveButtons();
  showToast('Google Drive desconectado.', 'info');
}

async function fetchSession() {
  try {
    const response = await fetch('/api/auth/session');
    if (!response.ok) {
      throw new Error('Falha ao validar sessao.');
    }
    const payload = await response.json();
    if (payload.success && payload.data) {
      state.currentUser = payload.data;
    }
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Falha ao validar sessao.', 'danger');
  }
}

function updateUserBanner() {
  if (!elements.currentUserName || !elements.currentUserRole) {
    return;
  }
  if (!state.currentUser) {
    elements.currentUserName.textContent = '';
    elements.currentUserRole.textContent = '';
    if (elements.btnManageUsers) {
      elements.btnManageUsers.classList.add('d-none');
    }
    return;
  }
  elements.currentUserName.textContent = state.currentUser.username;
  const roleLabel = state.currentUser.role === 'admin' ? 'Administrador' : 'Usuario';
  elements.currentUserRole.textContent = roleLabel;
  if (elements.btnManageUsers) {
    elements.btnManageUsers.classList.toggle('d-none', !state.currentUser.is_admin);
  }
}

async function submitLogout(event) {
  event?.preventDefault();
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error) {
    console.error(error);
  } finally {
    window.location.href = '/auth/login';
  }
}

async function openUserManager() {
  if (!state.currentUser || !state.currentUser.is_admin) {
    showToast('Somente administradores podem gerenciar usuarios.', 'warning');
    return;
  }
  await loadUsers();
  if (modals.userManager) {
    modals.userManager.show();
  }
}

async function loadUsers() {
  try {
    const response = await fetch('/api/users');
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Nao foi possivel listar usuarios.');
    }
    state.userList = payload.data || [];
    renderUserList(state.userList);
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Nao foi possivel listar usuarios.', 'danger');
  }
}

function renderUserList(users) {
  if (!elements.userListBody) {
    return;
  }
  if (!Array.isArray(users) || users.length === 0) {
    elements.userListBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">Nenhum usuario encontrado.</td></tr>';
    return;
  }
  const rows = users.map((user) => {
    const statusBadge = user.is_active ? '<span class="badge bg-success">Ativo</span>' : '<span class="badge bg-secondary">Inativo</span>';
    const toggleLabel = user.is_active ? 'Desativar' : 'Ativar';
    return `
      <tr data-username="${user.username}">
        <td>${user.username}</td>
        <td>${user.role === 'admin' ? 'Administrador' : 'Usuario'}</td>
        <td>${statusBadge}</td>
        <td class="text-end">
          <div class="btn-group btn-group-sm" role="group">
            <button type="button" class="btn btn-outline-secondary" data-action="toggle">${toggleLabel}</button>
            <button type="button" class="btn btn-outline-primary" data-action="reset">Redefinir senha</button>
          </div>
        </td>
      </tr>`;
  });
  elements.userListBody.innerHTML = rows.join('');
}

async function handleUserCreate(event) {
  event.preventDefault();
  if (!elements.userCreateUsername || !elements.userCreatePassword || !elements.userCreateRole) {
    return;
  }
  const username = elements.userCreateUsername.value.trim();
  const password = elements.userCreatePassword.value.trim();
  const role = elements.userCreateRole.value;
  if (!username || !password) {
    showToast('Informe usuario e senha.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Nao foi possivel criar o usuario.');
    }
    showToast('Usuario criado com sucesso.', 'success');
    elements.userCreateForm.reset();
    await loadUsers();
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Nao foi possivel criar o usuario.', 'danger');
  }
}

async function handleUserAction(event) {
  const button = event.target.closest('button[data-action]');
  if (!button) {
    return;
  }
  const row = button.closest('tr');
  const username = row ? row.dataset.username : null;
  if (!username) {
    return;
  }
  const action = button.dataset.action;
  if (action === 'toggle') {
    const user = state.userList.find((item) => item.username === username);
    const nextState = !(user && user.is_active);
    await toggleUserStatus(username, nextState);
  } else if (action === 'reset') {
    await resetUserPassword(username);
  }
}

async function toggleUserStatus(username, active) {
  try {
    const response = await fetch(`/api/users/${encodeURIComponent(username)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Nao foi possivel atualizar o usuario.');
    }
    showToast(`Usuario ${active ? 'ativado' : 'desativado'}.`, 'success');
    await loadUsers();
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Nao foi possivel atualizar o usuario.', 'danger');
  }
}

async function resetUserPassword(username) {
  const newPassword = window.prompt(`Informe a nova senha para ${username}`);
  if (!newPassword) {
    return;
  }
  if (newPassword.trim().length < 6) {
    showToast('Senha deve ter ao menos 6 caracteres.', 'warning');
    return;
  }
  try {
    const response = await fetch(`/api/users/${encodeURIComponent(username)}/password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: newPassword.trim() }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Nao foi possivel atualizar a senha.');
    }
    showToast('Senha redefinida.', 'success');
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Nao foi possivel atualizar a senha.', 'danger');
  }
}
async function reloadDirectory(forceReset = false, options = {}) {
  if (forceReset) {
    state.page = 1;
    state.hasMore = true;
  }
  return loadDirectory(forceReset, options);
}

async function loadDirectory(reset = false, options = {}) {
  if (state.isLoading) {
    return;
  }
  if (!state.hasMore && !reset) {
    return;
  }
  if (reset && !options.preserveSelection) {
    clearMultiSelection();
  }
  setLoading(!options.silent);

  const params = new URLSearchParams({
    path: state.currentPath || '',
    sort_by: state.sortBy,
    direction: state.direction,
    page: state.page.toString(),
    page_size: state.pageSize.toString(),
  });
  if (state.search) {
    params.set('search', state.search);
  }
  if (state.statusFilter.length > 0) {
    params.set('status', state.statusFilter.join(','));
  }

  try {
    const response = await fetch(`/api/list_items?${params.toString()}`);
    if (!response.ok) {
      throw new Error('Falha ao carregar itens.');
    }
    const payload = await response.json();
    if (!payload.success) {
      throw new Error(payload.error || 'Erro ao listar itens.');
    }

    const { data } = payload;
    const {
      items,
      current_path: currentPath,
      parent_path: parentPath,
      total,
      has_more: hasMore,
    } = data;

    if (!state.currentPath || reset) {
      state.currentPath = currentPath;
    }
    state.parentPath = parentPath;
    if (elements.addressInput) {
      elements.addressInput.value = state.currentPath;
    }
    localStorage.setItem('docmgr-last-path', state.currentPath);

    if (reset) {
      elements.tableBody.innerHTML = '';
      state.page = 1;
      state.selectedPath = options.preserveSelection ? state.selectedPath : null;
    }

    renderBreadcrumbs(data.breadcrumbs);
    renderItems(items, { reset });

    state.hasMore = hasMore;
    if (state.hasMore) {
      state.page += 1;
    }

    attachSentinel();
    updateSummary(total, currentPath);

    if (reset && items.length > 0) {
      const targetPath = options.preserveSelection && state.selectedPath ? state.selectedPath : items[0].path;
      focusRow(targetPath);
    }
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  } finally {
    setLoading(false);
  }
}
function renderItems(items, { reset }) {
  const fragment = document.createDocumentFragment();

  items.forEach((item) => {
    const row = document.createElement('tr');
    row.dataset.path = item.path;
    row.dataset.type = item.type;

    const isSelected = state.selectedPaths.has(item.path);
    if (isSelected) {
      row.classList.add('multi-selected');
    }

    if (state.selectedPath === item.path) {
      row.classList.add('active');
    }

    const selectCell = document.createElement('td');
    selectCell.className = 'text-center align-middle';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'form-check-input row-select';
    checkbox.checked = isSelected;
    checkbox.addEventListener('click', (event) => {
      event.stopPropagation();
    });
    checkbox.addEventListener('change', () => {
      toggleItemSelection(item.path, checkbox.checked, row);
    });
    selectCell.appendChild(checkbox);
    row.appendChild(selectCell);

    const nameCell = document.createElement('td');
    nameCell.innerHTML = `<i class="${item.icon} text-primary me-2"></i>${item.name}`;
    row.appendChild(nameCell);

    const sizeCell = document.createElement('td');
    sizeCell.className = 'text-end text-nowrap';
    sizeCell.textContent = item.size;
    row.appendChild(sizeCell);

    const modifiedCell = document.createElement('td');
    modifiedCell.className = 'text-end text-nowrap';
    modifiedCell.textContent = item.modified;
    row.appendChild(modifiedCell);

    const validityCell = document.createElement('td');
    validityCell.className = 'text-end text-nowrap';
    validityCell.textContent = item.validity;
    row.appendChild(validityCell);

    const statusCell = document.createElement('td');
    statusCell.className = 'text-end text-nowrap';
    const badge = document.createElement('span');
    badge.className = `badge bg-${item.status.color} badge-status`;
    badge.textContent = `${item.status.icon} ${item.status.label}`;
    statusCell.appendChild(badge);
    row.appendChild(statusCell);

    row.addEventListener('click', () => {
      selectRow(row);
    });

    row.addEventListener('dblclick', () => {
      if (item.type === 'directory') {
        state.currentPath = item.path;
        reloadDirectory(true);
      }
    });

    fragment.appendChild(row);
  });

  if (reset) {
    elements.tableBody.innerHTML = '';
  }

  elements.tableBody.appendChild(fragment);

  updateSelectionIndicators();
}

function escapeForSelector(value) {
  if (window.CSS && typeof window.CSS.escape === 'function') {
    return window.CSS.escape(value);
  }
  return value.replace(/([\0-\x1F\x7F"'\\#.:;?@\[\]^`{|}~])/g, '\\$1');
}

function toggleItemSelection(path, selected, row) {
  if (!path) {
    return;
  }
  if (selected) {
    state.selectedPaths.add(path);
  } else {
    state.selectedPaths.delete(path);
  }

  if (!row) {
    const selector = `tr[data-path="${escapeForSelector(path)}"]`;
    row = elements.tableBody.querySelector(selector);
  }
  if (row) {
    row.classList.toggle('multi-selected', state.selectedPaths.has(path));
    const checkbox = row.querySelector('input.row-select');
    if (checkbox && checkbox.checked !== selected) {
      checkbox.checked = selected;
    }
  }

  updateSelectionIndicators();
}

function handleSelectAllChange(event) {
  const checked = event.target.checked;
  const checkboxes = elements.tableBody ? elements.tableBody.querySelectorAll('input.row-select') : [];
  if (!checkboxes.length) {
    updateSelectionIndicators();
    return;
  }
  if (!checked) {
    state.selectedPaths.clear();
  }
  checkboxes.forEach((checkbox) => {
    checkbox.checked = checked;
    const row = checkbox.closest('tr');
    const path = row ? row.dataset.path : null;
    if (!path) {
      return;
    }
    if (checked) {
      state.selectedPaths.add(path);
    } else {
      state.selectedPaths.delete(path);
    }
    if (row) {
      row.classList.toggle('multi-selected', checked);
    }
  });
  updateSelectionIndicators();
}

function clearMultiSelection() {
  if (state.selectedPaths.size === 0) {
    updateSelectionIndicators();
    return;
  }
  state.selectedPaths.clear();
  if (elements.tableBody) {
    elements.tableBody.querySelectorAll('tr').forEach((row) => row.classList.remove('multi-selected'));
    elements.tableBody.querySelectorAll('input.row-select').forEach((checkbox) => {
      checkbox.checked = false;
    });
  }
  if (elements.selectAllRows) {
    elements.selectAllRows.checked = false;
    elements.selectAllRows.indeterminate = false;
  }
  updateSelectionIndicators();
}

function getSelectedPaths() {
  return Array.from(state.selectedPaths);
}

function getValidityTargetPaths() {
  const selectedPaths = getSelectedPaths().filter((path) => typeof path === 'string' && path.trim() !== '');
  if (selectedPaths.length > 0) {
    return selectedPaths;
  }
  return state.selectedPath ? [state.selectedPath] : [];
}

function updateSelectionIndicators() {
  const selectedCount = state.selectedPaths.size;
  if (elements.btnMove) {
    elements.btnMove.disabled = selectedCount === 0;
  }
  if (elements.btnCopy) {
    elements.btnCopy.disabled = selectedCount === 0;
  }
  if (elements.btnDelete) {
    elements.btnDelete.disabled = selectedCount === 0;
  }

  if (elements.selectAllRows) {
    const checkboxes = elements.tableBody ? elements.tableBody.querySelectorAll('input.row-select') : [];
    if (!checkboxes.length) {
      elements.selectAllRows.checked = false;
      elements.selectAllRows.indeterminate = false;
    } else {
      const checkedCount = Array.from(checkboxes).filter((checkbox) => checkbox.checked).length;
      elements.selectAllRows.checked = checkedCount > 0 && checkedCount === checkboxes.length;
      elements.selectAllRows.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
    }
  }

  if (elements.transferSelectionSummary) {
    if (selectedCount === 0) {
      elements.transferSelectionSummary.textContent = '';
    } else {
      const label = selectedCount === 1 ? 'item selecionado' : 'itens selecionados';
      elements.transferSelectionSummary.textContent = `${selectedCount} ${label}`;
    }
  }
}

function openTransferModal(mode) {
  const selected = getSelectedPaths();
  if (selected.length === 0) {
    showToast('Selecione ao menos um item para continuar.', 'warning');
    return;
  }
  if (!modals.transfer) {
    showToast('Modal de transferência indisponível.', 'danger');
    return;
  }

  state.transferMode = mode;
  state.transferTargetPath = state.currentPath || '';
  state.transferParentPath = '';

  if (elements.transferModalTitle) {
    elements.transferModalTitle.textContent = mode === 'move' ? 'Mover itens' : 'Copiar itens';
  }
  if (elements.btnConfirmTransfer) {
    elements.btnConfirmTransfer.textContent = mode === 'move' ? 'Mover' : 'Copiar';
    elements.btnConfirmTransfer.disabled = false;
  }
  if (elements.transferPathInput) {
    elements.transferPathInput.value = state.transferTargetPath;
  }

  updateSelectionIndicators();
  loadTransferDirectory(state.transferTargetPath);
  modals.transfer.show();
}

function navigateTransferPath(path) {
  const target = (typeof path === 'string' ? path : (elements.transferPathInput ? elements.transferPathInput.value : '')).trim();
  if (!target) {
    showToast('Informe um destino válido.', 'warning');
    return;
  }
  loadTransferDirectory(target);
}

function navigateTransferParent() {
  if (!state.transferTargetPath) {
    navigateTransferPath(state.currentPath || '');
    return;
  }
  const parent = state.transferParentPath;
  if (!parent || parent === state.transferTargetPath) {
    return;
  }
  navigateTransferPath(parent);
}

async function loadTransferDirectory(path) {
  const target = path || state.transferTargetPath || state.currentPath || '';
  if (!target) {
    showToast('Caminho inválido.', 'warning');
    return;
  }
  if (elements.transferDirectoryList) {
    elements.transferDirectoryList.innerHTML = '<div class="list-group-item text-muted">Carregando...</div>';
  }
  try {
    const params = new URLSearchParams({
      path: target,
      sort_by: 'name',
      direction: 'asc',
      page: '1',
      page_size: '200',
    });
    const response = await fetch(`/api/list_items?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível listar pastas.');
    }
    const { data } = payload;
    state.transferTargetPath = data.current_path;
    state.transferParentPath = data.parent_path;
    if (elements.transferPathInput) {
      elements.transferPathInput.value = state.transferTargetPath;
    }
    const directories = (data.items || []).filter((item) => item.type === 'directory');
    renderTransferDirectories(directories);
  } catch (error) {
    console.error(error);
    if (elements.transferDirectoryList) {
      elements.transferDirectoryList.innerHTML = '<div class="list-group-item text-danger">Erro ao carregar pastas.</div>';
    }
    showToast(error.message || 'Não foi possível carregar o destino.', 'danger');
  }
}

function renderTransferDirectories(directories) {
  if (!elements.transferDirectoryList) {
    return;
  }
  elements.transferDirectoryList.innerHTML = '';
  if (!directories || directories.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'list-group-item text-muted';
    empty.textContent = 'Nenhuma pasta encontrada.';
    elements.transferDirectoryList.appendChild(empty);
    return;
  }
  directories.forEach((directory) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
    button.innerHTML = `<span><i class="bi bi-folder text-warning me-2"></i>${directory.name}</span><i class="bi bi-chevron-right"></i>`;
    button.addEventListener('click', () => {
      navigateTransferPath(directory.path);
    });
    elements.transferDirectoryList.appendChild(button);
  });
}

async function createFolderInTransfer() {
  const parent = (elements.transferPathInput ? elements.transferPathInput.value : state.transferTargetPath || '').trim();
  if (!parent) {
    showToast('Navegue até uma pasta antes de criar uma nova.', 'warning');
    return;
  }
  const name = window.prompt('Nome da nova pasta:');
  if (!name) {
    return;
  }
  const trimmedName = name.trim();
  if (!trimmedName) {
    showToast('Informe um nome válido.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/create_folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent, name: trimmedName }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível criar a pasta.');
    }
    showToast('Pasta criada!', 'success');
    await loadTransferDirectory(parent);
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Não foi possível criar a pasta.', 'danger');
  }
}

async function submitTransferAction() {
  if (!state.transferMode) {
    return;
  }
  const destination = (elements.transferPathInput ? elements.transferPathInput.value : '').trim();
  const paths = getSelectedPaths();
  if (!destination) {
    showToast('Informe a pasta destino.', 'warning');
    return;
  }
  if (paths.length === 0) {
    showToast('Selecione itens para transferir.', 'warning');
    return;
  }

  const endpoint = state.transferMode === 'move' ? '/api/items/move' : '/api/items/copy';
  try {
    if (elements.btnConfirmTransfer) {
      elements.btnConfirmTransfer.disabled = true;
    }
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destination, paths }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'A transferência falhou.');
    }
    modals.transfer.hide();
    showToast(state.transferMode === 'move' ? 'Itens movidos!' : 'Itens copiados!', 'success');
    clearMultiSelection();
    clearDetails();
    await reloadDirectory(true);
  } catch (error) {
    console.error(error);
    showToast(error.message || 'A transferência falhou.', 'danger');
  } finally {
    if (elements.btnConfirmTransfer) {
      elements.btnConfirmTransfer.disabled = false;
    }
  }
}

function selectRow(row) {
  if (!row) {
    return;
  }
  elements.tableBody.querySelectorAll('tr').forEach((tr) => tr.classList.remove('active'));
  row.classList.add('active');
  state.selectedPath = row.dataset.path;
  state.selectedType = row.dataset.type;
  loadDetails();
  updateActionButtons();
}

function focusRow(path) {
  if (!path) {
    return;
  }
  const row = [...elements.tableBody.querySelectorAll('tr')].find((tr) => tr.dataset.path === path);
  if (row) {
    selectRow(row);
    row.scrollIntoView({ block: 'nearest' });
  } else if (elements.tableBody.firstElementChild) {
    selectRow(elements.tableBody.firstElementChild);
  } else {
    clearDetails();
  }
}

function renderBreadcrumbs(breadcrumbs) {
  elements.breadcrumb.innerHTML = '';
  if (!breadcrumbs || breadcrumbs.length === 0) {
    return;
  }
  breadcrumbs.forEach((crumb, index) => {
    const li = document.createElement('li');
    li.className = `breadcrumb-item ${index === breadcrumbs.length - 1 ? 'active' : ''}`;
    if (index === breadcrumbs.length - 1) {
      li.textContent = crumb.label || crumb.path;
    } else {
      const link = document.createElement('a');
      link.href = '#';
      link.textContent = crumb.label || crumb.path;
      link.addEventListener('click', (event) => {
        event.preventDefault();
        state.currentPath = crumb.path;
        reloadDirectory(true);
      });
      li.appendChild(link);
    }
    elements.breadcrumb.appendChild(li);
  });
}

function updateSummary(total, path) {
  elements.itemsSummary.textContent = `${total} itens em ${path}`;
}

function updateSortIndicators() {
  document.querySelectorAll('#itemsTable thead th.sortable').forEach((header) => {
    const icon = header.querySelector('.bi');
    header.classList.toggle('active', header.dataset.sort === state.sortBy);
    if (!icon) {
      return;
    }
    if (header.dataset.sort === state.sortBy) {
      icon.className = `bi ${state.direction === 'asc' ? 'bi-arrow-up' : 'bi-arrow-down'}`;
    } else {
      icon.className = 'bi';

    }
  });
}

async function loadDetails() {
  if (!state.selectedPath) {
    clearDetails();
    return;
  }
  try {
    const params = new URLSearchParams({ path: state.selectedPath });
    const response = await fetch(`/api/details?${params.toString()}`);
    if (!response.ok) {
      throw new Error('Não foi possível obter os detalhes.');
    }
    const payload = await response.json();
    if (!payload.success) {
      throw new Error(payload.error || 'Erro ao obter detalhes.');
    }
    state.detail = payload.data;
    fillDetails(payload.data);
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

function fillDetails(detail) {
  elements.detailName.textContent = detail.name;
  elements.detailPath.textContent = detail.path;
  elements.detailSize.textContent = `Tamanho: ${detail.size}`;
  elements.detailModified.textContent = `Modificado: ${detail.modified}`;
  let validityText = detail.validity;
  if (
    detail.validity_type === 'defined' &&
    typeof detail.validity_days_remaining === 'number' &&
    Number.isFinite(detail.validity_days_remaining)
  ) {
    const days = detail.validity_days_remaining;
    let message;
    if (days > 1) {
      message = `${days} dias restantes`;
    } else if (days === 1) {
      message = '1 dia restante';
    } else if (days === 0) {
      message = 'Vence hoje';
    } else if (days === -1) {
      message = 'Vencido há 1 dia';
    } else {
      message = `Vencido há ${Math.abs(days)} dias`;
    }
    validityText = `${detail.validity} (${message})`;
  }
  elements.detailValidity.textContent = validityText;
  elements.detailWarningDays.textContent = detail.warning_days;
  elements.detailStatus.textContent = `${detail.status.icon} ${detail.status.label}`;
  elements.detailStatus.className = `badge bg-${detail.status.color}`;
  elements.notesInput.value = detail.notes || '';
  state.notesSnapshot = detail.notes || '';
}

function clearDetails() {
  state.detail = null;
  state.selectedPath = null;
  state.selectedType = null;
  elements.detailName.textContent = 'Selecione um item';
  elements.detailPath.textContent = '';
  elements.detailSize.textContent = '';
  elements.detailModified.textContent = '';
  elements.detailValidity.textContent = '--';
  elements.detailWarningDays.textContent = '--';
  elements.detailStatus.textContent = '--';
  elements.detailStatus.className = 'badge bg-secondary';
  elements.notesInput.value = '';
  state.notesSnapshot = '';
  updateActionButtons();
}

function updateActionButtons() {
  const driveMode = isGoogleDrivePath();
  const enabled = Boolean(state.selectedPath);
  [
    elements.btnSaveNotes,
    elements.btnResetNotes,
    elements.btnOpenFile,
    elements.btnOpenFolder,
    elements.btnSetValidity,
    elements.btnMarkIndeterminate,
    elements.btnClearValidity,
    elements.btnRename,
  ].forEach((button) => {
    if (button) {
      button.disabled = !enabled;
    }
  });

  if (driveMode) {
    [
      elements.btnSaveNotes,
      elements.btnResetNotes,
      elements.btnSetValidity,
      elements.btnMarkIndeterminate,
      elements.btnClearValidity,
      elements.btnRename,
      elements.btnMove,
      elements.btnCopy,
      elements.btnDelete,
      elements.btnUpload,
      elements.btnNewFolder,
      elements.btnNewFile,
      elements.btnExport,
    ].forEach((button) => {
      if (button) button.disabled = true;
    });
  } else {
    [
      elements.btnMove,
      elements.btnCopy,
      elements.btnUpload,
      elements.btnNewFolder,
      elements.btnNewFile,
      elements.btnExport,
    ].forEach((button) => {
      if (button) button.disabled = false;
    });
  }

  if (elements.btnOpenFile) {
    elements.btnOpenFile.disabled = !enabled || state.selectedType !== 'file';
  }
  if (elements.btnOpenFolder) {
    elements.btnOpenFolder.disabled = !enabled || state.selectedType !== 'directory';
  }
}

async function saveNotes() {
  if (isGoogleDrivePath()) {
    showToast('Observacoes em arquivos do Google Drive ainda nao estao habilitadas.', 'info');
    return;
  }
  if (!state.selectedPath) {
    return;
  }
  const notes = elements.notesInput.value;
  try {
    const response = await fetch('/api/set_notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: state.selectedPath, notes }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível salvar as observações.');
    }
    state.notesSnapshot = payload.data.notes;
    showToast('Observações salvas!', 'success');
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

function resetNotes() {
  elements.notesInput.value = state.notesSnapshot;
}

async function triggerSimpleAction(endpoint) {
  if (!state.selectedPath) {
    return;
  }
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: state.selectedPath }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Falha ao executar ação.');
    }
    if (payload?.data?.url) {
      window.open(payload.data.url, '_blank', 'noopener');
      showToast('Aberto no Google Drive.', 'success');
      return;
    }
    showToast('Ação executada com sucesso.', 'success');
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function quickValidity(type) {
  if (isGoogleDrivePath()) {
    showToast('Validade em arquivos do Google Drive ainda nao esta habilitada.', 'info');
    return;
  }
  const paths = getValidityTargetPaths();
  if (paths.length === 0) {
    showToast('Selecione ao menos um item para atualizar a validade.', 'warning');
    return;
  }

  let updatedCount = 0;
  let firstError = null;

  try {
    for (const path of paths) {
      const response = await fetch('/api/set_validity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path,
          validity_type: type,
        }),
      });

      const payload = await response.json();
      if (!response.ok || !payload.success) {
        const errorMessage = payload.error || `Não foi possível atualizar a validade de "${path}".`;
        if (!firstError) {
          firstError = errorMessage;
        }
        continue;
      }
      updatedCount += 1;
    }

    if (updatedCount === 0) {
      throw new Error(firstError || 'Não foi possível atualizar a validade.');
    }

    if (firstError) {
      showToast(`${updatedCount} de ${paths.length} item(ns) atualizado(s). ${firstError}`, 'warning');
    } else if (updatedCount === 1) {
      showToast('Validade atualizada.', 'success');
    } else {
      showToast(`Validade atualizada em ${updatedCount} itens.`, 'success');
    }

    await reloadDirectory(true, { preserveSelection: true });
    if (state.selectedPath) {
      await loadDetails();
    }
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function submitValidity() {
  if (isGoogleDrivePath()) {
    showToast('Validade em arquivos do Google Drive ainda nao esta habilitada.', 'info');
    return;
  }
  if (!state.selectedPath) {
    return;
  }
  const selected = elements.validityForm.querySelector('input[name="validityType"]:checked');
  const type = selected ? selected.value : 'not_defined';
  let validity = null;
  if (type === 'defined') {
    validity = elements.validityDate.value.trim();
    if (!validity) {
      showToast('Informe a data de validade.', 'warning');
      return;
    }
  }
  const warningDays = parseInt(elements.warningDays.value, 10) || state.globalWarningDays;
  try {
    const response = await fetch('/api/set_validity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: state.selectedPath,
        validity_type: type,
        validity,
        warning_days: warningDays,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível salvar a validade.');
    }
    modals.validity.hide();
    showToast('Validade definida com sucesso.', 'success');
    await reloadDirectory(true, { preserveSelection: true });
    await loadDetails();
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function submitRename() {
  if (isGoogleDrivePath()) {
    showToast('Renomear no Google Drive ainda nao esta habilitado.', 'info');
    return;
  }
  if (!state.selectedPath) {
    return;
  }
  const originalPath = state.selectedPath;
  const newName = elements.renameInput.value.trim();
  if (!newName) {
    showToast('Informe um novo nome.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: state.selectedPath, new_name: newName }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível renomear.');
    }
    modals.rename.hide();
    showToast('Item renomeado.', 'success');
    if (state.selectedPaths.has(originalPath)) {
      state.selectedPaths.delete(originalPath);
      state.selectedPaths.add(payload.data.path);
    }
    state.selectedPath = payload.data.path;
    await reloadDirectory(true, { preserveSelection: true });
    await loadDetails();
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function submitCreateFolder() {
  if (isGoogleDrivePath()) {
    showToast('Criar pasta no Google Drive ainda nao esta habilitado.', 'info');
    return;
  }
  const name = elements.newFolderName.value.trim();
  if (!name) {
    showToast('Informe o nome da pasta.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/create_folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent: state.currentPath, name }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível criar a pasta.');
    }
    modals.createFolder.hide();
    showToast('Pasta criada!', 'success');
    await reloadDirectory(true, { preserveSelection: true });
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function submitCreateFile() {
  if (isGoogleDrivePath()) {
    showToast('Criar arquivo no Google Drive ainda nao esta habilitado.', 'info');
    return;
  }
  const name = elements.newFileName.value.trim();
  if (!name) {
    showToast('Informe o nome do arquivo.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/create_file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent: state.currentPath, name }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível criar o arquivo.');
    }
    modals.createFile.hide();
    showToast('Arquivo criado!', 'success');
    await reloadDirectory(true, { preserveSelection: true });
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function submitUpload() {
  if (isGoogleDrivePath()) {
    showToast('Upload direto para Google Drive ainda nao esta habilitado.', 'info');
    return;
  }
  const files = elements.uploadInput.files;
  if (!files || files.length === 0) {
    showToast('Selecione ao menos um arquivo.', 'warning');
    return;
  }
  const formData = new FormData();
  formData.append('path', state.currentPath);
  Array.from(files).forEach((file) => {
    formData.append('files', file);
  });
  try {
    const response = await fetch('/api/upload', {
      method: 'POST',
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Falha no upload.');
    }
    modals.upload.hide();
    elements.uploadInput.value = '';
    const uploadedItems = Array.isArray(payload.data) ? payload.data : [];
    const autoValidityCount = uploadedItems.filter((item) => item && item.auto_validity).length;
    if (autoValidityCount > 0) {
      const label = autoValidityCount === 1 ? 'arquivo' : 'arquivos';
      showToast(`Upload concluído! Validade automática aplicada em ${autoValidityCount} ${label}.`, 'success');
    } else {
      showToast('Upload concluído!', 'success');
    }
    await reloadDirectory(true, { preserveSelection: true });
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}
async function submitDelete() {
  const selected = getSelectedPaths();
  if (selected.length === 0) {
    showToast('Selecione ao menos um item para excluir.', 'warning');
    return;
  }
  const message = selected.length === 1
    ? 'Tem certeza que deseja excluir este item?'
    : `Tem certeza que deseja excluir ${selected.length} itens?`;
  const confirmed = window.confirm(message);
  if (!confirmed) {
    return;
  }
  try {
    const response = await fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: selected }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Nao foi possivel excluir.');
    }
    selected.forEach((path) => state.selectedPaths.delete(path));
    state.selectedPath = null;
    updateSelectionIndicators();
    await reloadDirectory(true, { preserveSelection: true });
    clearDetails();
    const toastMessage = selected.length === 1
      ? 'Item excluido.'
      : `${selected.length} itens excluidos.`;
    showToast(toastMessage, 'success');
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}


function applyStatusFilter() {
  const selected = [...elements.statusFilter.querySelectorAll('input[type="checkbox"]:checked')].map((checkbox) => checkbox.value);
  state.statusFilter = selected;
  const label = selected.length ? `${selected.length} selecionado(s)` : 'Filtrar status';
  document.getElementById('statusFilterBtn').textContent = label;
  reloadDirectory(true);
}

async function handleExport() {
  if (isGoogleDrivePath()) {
    showToast('Exportar CSV do Google Drive ainda nao esta habilitado.', 'info');
    return;
  }
  if (!state.currentPath) {
    return;
  }
  window.open(`/api/export?path=${encodeURIComponent(state.currentPath)}`, '_blank');
}

async function fetchWarningDays() {
  try {
    const response = await fetch('/api/settings/warning_days');
    if (!response.ok) {
      throw new Error('Erro ao carregar configuração de alerta.');
    }
    const payload = await response.json();
    if (payload.success) {
      state.globalWarningDays = payload.data.warning_days;
    }
  } catch (error) {
    console.warn(error);
  }
}

async function submitWarningDays() {
  const value = parseInt(elements.globalWarningDays.value, 10);
  if (!value || value <= 0) {
    showToast('Informe um valor válido.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/settings/warning_days', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ warning_days: value }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível salvar.');
    }
    state.globalWarningDays = payload.data.warning_days;
    modals.warningDays.hide();
    showToast('Alerta atualizado!', 'success');
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

function openWarningSettings() {
  elements.globalWarningDays.value = state.globalWarningDays;
  modals.warningDays.show();
}

function openPregaoModal() {
  if (!state.currentPath) {
    showToast('Nenhum caminho selecionado.', 'warning');
    return;
  }
  modals.pregao.show();
}

async function submitPregao() {
  const name = elements.pregaoName.value.trim();
  const path = (elements.pregaoPath && elements.pregaoPath.value.trim()) || state.currentPath || '';
  if (!name) {
    showToast('Informe o nome do pregão.', 'warning');
    return;
  }
  if (!path) {
    showToast('Informe o caminho da pasta.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, path }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível salvar o pregão.');
    }
    modals.pregao.hide();
    showToast('Pregão salvo!', 'success');
    await fetchPregoes();
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function fetchPregoes() {
  try {
    const response = await fetch('/api/presets');
    if (!response.ok) {
      throw new Error('Não foi possível carregar os pregões.');
    }
    const payload = await response.json();
    if (!payload.success) {
      throw new Error(payload.error || 'Erro ao carregar pregões.');
    }
    state.pregoes = payload.data;
    renderPregoes(state.pregoes);
  } catch (error) {
    console.error(error);
  }
}

function renderPregoes(pregoes) {
  if (!elements.pregaoList) {
    return;
  }
  elements.pregaoList.innerHTML = '';
  if (!pregoes || pregoes.length === 0) {
    state.selectedPregao = null;
    const empty = document.createElement('li');
    empty.className = 'dropdown-item text-muted';
    empty.textContent = 'Nenhum pregão cadastrado';
    elements.pregaoList.appendChild(empty);
    updatePregaoSelectionButton();
    return;
  }

  pregoes.forEach((pregao) => {
    const item = document.createElement('li');
    const wrapper = document.createElement('div');
    wrapper.className = 'd-flex align-items-center justify-content-between gap-2 px-3 py-1';

    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'btn btn-link text-start flex-grow-1';
    link.textContent = pregao.name;
    if (state.selectedPregao && state.selectedPregao.path === pregao.path) {
      link.classList.add('fw-semibold', 'text-primary');
    }
    link.addEventListener('click', () => {
      state.currentPath = pregao.path;
      state.selectedPregao = { name: pregao.name, path: pregao.path };
      state.selectedFavorite = null;
      const inp = document.getElementById('addressInput');
      if (inp) inp.value = pregao.path;
      renderFavorites(state.favorites);
      updatePregaoSelectionButton();
      reloadDirectory(true);
    });

    const removeBtn = document.createElement('button');
    removeBtn.className = 'btn btn-sm btn-outline-danger';
    removeBtn.innerHTML = '<i class="bi bi-x"></i>';
    removeBtn.addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        const response = await fetch(`/api/presets/${pregao.id}`, {
          method: 'DELETE',
        });
        const payload = await response.json();
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || 'Erro ao remover pregão.');
        }
        showToast('Pregão removido.', 'success');
        await fetchPregoes();
      } catch (error) {
        console.error(error);
        showToast(error.message, 'danger');
      }
    });

    wrapper.appendChild(link);
    wrapper.appendChild(removeBtn);
    item.appendChild(wrapper);
    elements.pregaoList.appendChild(item);
  });

  if (state.selectedPregao && !pregoes.some((item) => item.path === state.selectedPregao.path)) {
    state.selectedPregao = null;
  }
  updatePregaoSelectionButton();
}

function openFavoriteModal() {
  if (!state.currentPath) {
    showToast('Nenhum caminho selecionado.', 'warning');
    return;
  }
  modals.favorite.show();
}

async function submitFavorite() {
  const name = elements.favoriteName.value.trim();
  const path = (elements.favoritePath && elements.favoritePath.value.trim()) || state.currentPath || '';
  if (!name) {
    showToast('Informe o nome do favorito.', 'warning');
    return;
  }
  if (!path) {
    showToast('Informe o caminho da pasta.', 'warning');
    return;
  }
  try {
    const response = await fetch('/api/favorites/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, path }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Não foi possível salvar o favorito.');
    }
    modals.favorite.hide();
    showToast('Favorito salvo!', 'success');
    await fetchFavorites();
  } catch (error) {
    console.error(error);
    showToast(error.message, 'danger');
  }
}

async function fetchFavorites() {
  try {
    const response = await fetch('/api/favorites/list');
    if (!response.ok) {
      throw new Error('Não foi possível carregar os favoritos.');
    }
    const payload = await response.json();
    if (!payload.success) {
      throw new Error(payload.error || 'Erro ao carregar favoritos.');
    }
    state.favorites = payload.data;
    renderFavorites(state.favorites);
  } catch (error) {
    console.error(error);
  }
}

function renderFavorites(favorites) {
  if (!elements.favoriteList) {
    return;
  }
  elements.favoriteList.innerHTML = '';
  if (!favorites || favorites.length === 0) {
    state.selectedFavorite = null;
    const empty = document.createElement('li');
    empty.className = 'dropdown-item text-muted';
    empty.textContent = 'Nenhum favorito cadastrado';
    elements.favoriteList.appendChild(empty);
    updateFavoriteSelectionButton();
    return;
  }

  favorites.forEach((favorite) => {
    const item = document.createElement('li');
    const wrapper = document.createElement('div');
    wrapper.className = 'd-flex align-items-center justify-content-between gap-2 px-3 py-1';

    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'btn btn-link text-start flex-grow-1';
    link.textContent = favorite.name;
    if (state.selectedFavorite && state.selectedFavorite.path === favorite.path) {
      link.classList.add('fw-semibold', 'text-primary');
    }
    link.addEventListener('click', () => {
      state.currentPath = favorite.path;
      state.selectedFavorite = { name: favorite.name, path: favorite.path };
      state.selectedPregao = null;
      const inp = document.getElementById('addressInput');
      if (inp) inp.value = favorite.path;
      renderPregoes(state.pregoes);
      updateFavoriteSelectionButton();
      reloadDirectory(true);
    });

    const removeBtn = document.createElement('button');
    removeBtn.className = 'btn btn-sm btn-outline-danger';
    removeBtn.innerHTML = '<i class="bi bi-x"></i>';
    removeBtn.addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        const response = await fetch('/api/favorites/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: favorite.name }),
        });
        const payload = await response.json();
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || 'Erro ao remover favorito.');
        }
        showToast('Favorito removido.', 'success');
        await fetchFavorites();
      } catch (error) {
        console.error(error);
        showToast(error.message, 'danger');
      }
    });

    wrapper.appendChild(link);
    wrapper.appendChild(removeBtn);
    item.appendChild(wrapper);
    elements.favoriteList.appendChild(item);
  });

  if (state.selectedFavorite && !favorites.some((item) => item.path === state.selectedFavorite.path)) {
    state.selectedFavorite = null;
  }
  updateFavoriteSelectionButton();
}

function updatePregaoSelectionButton() {
  if (!elements.pregaoDropdown) {
    return;
  }
  const hasSelection = Boolean(state.selectedPregao);
  elements.pregaoDropdown.textContent = hasSelection ? state.selectedPregao.name : 'Acessar pregão';
  elements.pregaoDropdown.classList.toggle('btn-primary', hasSelection);
  elements.pregaoDropdown.classList.toggle('btn-outline-secondary', !hasSelection);
}

function updateFavoriteSelectionButton() {
  if (!elements.favoriteDropdown) {
    return;
  }
  const hasSelection = Boolean(state.selectedFavorite);
  elements.favoriteDropdown.textContent = hasSelection ? state.selectedFavorite.name : 'Acessar favorito';
  elements.favoriteDropdown.classList.toggle('btn-primary', hasSelection);
  elements.favoriteDropdown.classList.toggle('btn-outline-secondary', !hasSelection);
}

function showToast(message, variant = 'primary') {
  const container = document.getElementById('toast-container');
  if (!container) {
    return;
  }
  const toastElement = document.createElement('div');
  toastElement.className = `toast align-items-center text-bg-${variant}`;
  toastElement.role = 'status';
  toastElement.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${message}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>`;
  container.appendChild(toastElement);
  const toast = new bootstrap.Toast(toastElement, { delay: 4000 });
  toast.show();
  toastElement.addEventListener('hidden.bs.toast', () => {
    toast.dispose();
    toastElement.remove();
  });
}

function navigateUpDirectory() {
  if (!state.currentPath) {
    showToast('Nenhuma pasta selecionada.', 'warning');
    return;
  }
  const parent = state.parentPath;
  if (!parent || parent === state.currentPath) {
    showToast('Você já está na pasta raiz.', 'info');
    return;
  }
  state.currentPath = parent;
  if (elements.addressInput) {
    elements.addressInput.value = parent;
  }
  reloadDirectory(true);
}

// Address bar navigation
function navigateToAddress() {
  const input = document.getElementById('addressInput');
  if (!input) return;
  const value = (input.value || '').trim();
  if (!value) {
    showToast('Informe um caminho para navegar.', 'warning');
    return;
  }
  state.currentPath = value;
  state.page = 1;
  try {
    reloadDirectory(true);
  } catch (e) {
    console.error(e);
    showToast('Não foi possível navegar para o caminho informado.', 'danger');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('addressInput');
  const btn = document.getElementById('btnGoAddress');
  if (btn) btn.addEventListener('click', navigateToAddress);
  if (input) {
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        navigateToAddress();
      }
    });
  }
  const breadcrumb = document.getElementById('breadcrumb');
  if (breadcrumb && input) {
    const obs = new MutationObserver(() => {
      // Keep address bar in sync with current state path
      if (typeof state?.currentPath === 'string') {
        input.value = state.currentPath || '';
      }
    });
    obs.observe(breadcrumb, { childList: true, subtree: true });
  }
});
