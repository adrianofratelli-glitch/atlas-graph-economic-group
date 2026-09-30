import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ValidationPage from './ValidationPage'
import './index.css'
import './pov-signature.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {window.location.pathname === '/evidence' ? <ValidationPage /> : <App />}
  </React.StrictMode>
)
