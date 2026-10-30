import { useLayoutEffect } from "react";
import { useSelector } from "react-redux";
import { useNavigate } from "react-router-dom";
import { Button, Typography } from "@material-ui/core";

const ProtectedRoute = ({ children }) => {
    const userData = useSelector((state) => state.user.data);    
    const navigate = useNavigate();
    const userRole = userData?.userRole;
    const hasAccess = userRole === "admin";
   
    useLayoutEffect(() => {      
        if(!hasAccess) {
            console.log("Restricted");
            navigate("/home");
        }
    }, []);

    const onClickHandle = () => {
        navigate("/");
    }

    return <>{hasAccess ? children : <> 
        <Typography variant="h3" style={{paddingLeft: "3rem", paddingTop: "3rem"}}>No Access!</Typography> 
        <div style={{padding: "3rem"}}>
            <Button
                variant="contained"
                color="textSecondary"
                onClick={onClickHandle}
                >
                    Back to Home
                </Button>
        </div>
        </>
    } </>;
};

export default ProtectedRoute;
