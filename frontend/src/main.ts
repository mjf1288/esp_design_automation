import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
// fonts.css before style.css so @font-face rules are registered before the
// rules that reference the families.
import './fonts.css'
import './style.css'
import {installSyntheticLabels} from './synthetic-labels'
createApp(App).use(createPinia()).mount('#app')
installSyntheticLabels(document.getElementById('app')!)
