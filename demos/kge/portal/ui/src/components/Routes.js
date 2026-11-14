import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { RequiredAuth } from './SecureRoute.js';
import LandingPage from "../pages/LandingPage/LandingPage";
import Search from '../pages/Search/Search';
import DataProductDetails from '../pages/DataProductDetails/DataProductDetails';
import AdminAccess from "../pages/AdminAccess/AdminAccess";
import Login from "../pages/Login";
import Certify from "../pages/Certify/Certify";
import ContextApp from "../pages/Context/ContextApp";
import AppGate, { KnowledgeRoot } from "./shell/AppGate";

/* `/` is the sign-in page until a person is signed in, then the Knowledge home (or, with the
   Knowledge home off, the single app). Every app's routes pass its gate: switched off, or not
   the person's, they show the shared view under the global bar. Discover keeps today's paths. */
const discover = (page) => <AppGate app="discover">{page}</AppGate>;

const AppRoutes = () => {
  return (
    <Routes>
        <Route path='/' element={<RequiredAuth signedOut={<Login />} />}>
          <Route index element={<KnowledgeRoot />} />
        </Route>
        <Route element={<RequiredAuth />}>
          <Route path='/knowledge/docs/*' element={<Navigate to="/" replace />} />
          <Route path='/docs/*' element={<Navigate to="/" replace />} />
          <Route path='/certify' element={<AppGate app="certify"><Certify /></AppGate>} />
          <Route path='/context' element={<AppGate app="context"><ContextApp /></AppGate>} />
          <Route path='/Search' element={discover(<Search />)} />
          <Route path='/home' element={discover(<LandingPage />)} />
          <Route path='/adminaccess' element={discover(<AdminAccess />)} />
          <Route path='/productDetails/:id' element={discover(<DataProductDetails />)} />
        </Route>
    </Routes>
  );
};

export default AppRoutes;
