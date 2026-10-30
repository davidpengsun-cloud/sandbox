import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { RequiredAuth } from './SecureRoute.js';
import LandingPage from "../pages/LandingPage/LandingPage";
import Search from '../pages/Search/Search';
import DataProductDetails from '../pages/DataProductDetails/DataProductDetails';
import AdminAccess from "../pages/AdminAccess/AdminAccess";
import Login from "../pages/Login";

const AppRoutes = () => {
  return (
    <Routes>
        <Route path='/' element={<Login />} />
        <Route element={<RequiredAuth />}>
          <Route path='/Search' element={<Search />} />
          <Route path='/home' element={<LandingPage />} />
          <Route path='/adminaccess' element={<AdminAccess />} />
          <Route path='/productDetails/:id' element={<DataProductDetails />} />
        </Route>
        
    </Routes>
  );
};

export default AppRoutes;
