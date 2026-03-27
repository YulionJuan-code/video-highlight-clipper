import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { LayoutList, Plus, Settings, Film } from 'lucide-react';
import TaskList from './pages/TaskList.jsx';
import NewTask from './pages/NewTask.jsx';
import TaskDetail from './pages/TaskDetail.jsx';
import SettingsPage from './pages/Settings.jsx';

const navItems = [
  { to: '/', icon: LayoutList, label: '项目列表' },
  { to: '/new', icon: Plus, label: '新建任务' },
  { to: '/settings', icon: Settings, label: '设置' },
];

function App() {
  const location = useLocation();

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Film size={28} className="brand-icon" />
          <span className="brand-text">视频精华提取</span>
        </div>
        <nav className="sidebar-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              item.to === '/'
                ? location.pathname === '/' || location.pathname.startsWith('/task/')
                : location.pathname === item.to;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={`nav-item ${isActive ? 'nav-item-active' : ''}`}
              >
                <Icon size={20} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="version-text">v1.0.0</span>
        </div>
      </aside>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<TaskList />} />
          <Route path="/new" element={<NewTask />} />
          <Route path="/task/:id" element={<TaskDetail />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
