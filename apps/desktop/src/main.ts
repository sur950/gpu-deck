/** Connect screen entry point (bundled into the app; loaded in the `connect` window). */

import './styles.css';
import { ConnectApp } from './app';
import { tauriBackend } from './lib/ipc';

const root = document.getElementById('app');
if (root) {
  const app = new ConnectApp(root, tauriBackend);
  void app.start();
}
