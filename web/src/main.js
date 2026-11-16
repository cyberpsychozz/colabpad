import './styles.css';
import { $ } from './ui/dom.js';

// T0 skeleton: the world UI (camera, chunks, ALIVE) is built from T3 on.
const root = $('#app');
const page = document.createElement('div');
page.className = 'home';
const header = document.createElement('header');
header.className = 'site-header';
const brand = document.createElement('a');
brand.className = 'brand';
brand.href = '/';
brand.textContent = 'collabpad';
header.append(brand);
page.append(header);
root.replaceChildren(page);
