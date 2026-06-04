import { AppProvider, useApp } from './context/AppContext'
import { AlertsProvider } from './context/AlertsContext'
import { ChatProvider } from './context/ChatContext'
import { CompanyDataProvider } from './context/CompanyDataContext'
import { Shell } from './components/layout/Shell'
import { ChatPanel } from './components/modules/chat/ChatPanel'
import { ChatFAB } from './components/modules/chat/ChatFAB'
import { OverviewPage } from './pages/OverviewPage'
import { FleetPage } from './pages/FleetPage'
import { MaintenancePage } from './pages/MaintenancePage'
import { KPIsPage } from './pages/KPIsPage'
import { FuelPage } from './pages/FuelPage'
import { GPSPage } from './pages/GPSPage'
import { SafetyPage } from './pages/SafetyPage'
import { LoadTonnagePage } from './pages/LoadTonnagePage'

function PageRouter() {
  const { activeModule } = useApp()

  const pages = {
    overview: <OverviewPage />,
    fleet: <FleetPage />,
    maintenance: <MaintenancePage />,
    kpis: <KPIsPage />,
    'load-tonnage': <LoadTonnagePage />,
    fuel: <FuelPage />,
    gps: <GPSPage />,
    safety: <SafetyPage />,
  }

  return pages[activeModule] ?? <OverviewPage />
}

export function App() {
  return (
    <AppProvider>
      <AlertsProvider>
        <CompanyDataProvider>
        <ChatProvider>
          <Shell>
            <PageRouter />
          </Shell>
          <ChatPanel />
          <ChatFAB />
        </ChatProvider>
        </CompanyDataProvider>
      </AlertsProvider>
    </AppProvider>
  )
}
