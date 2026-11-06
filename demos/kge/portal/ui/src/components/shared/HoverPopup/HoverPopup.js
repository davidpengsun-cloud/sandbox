import { Grid, Paper, Typography } from '@material-ui/core';
import './HoverPopup.css';

export default function HoverPopup(props) {
    return (
        <Grid container className='hoverPopupMain' spacing={2}>
            <Grid item xs={6} className="hoverPopupSub"><Typography variant="h5" className="subTitle" color='textSecondary'>Publish Date</Typography>
                <Typography variant="h4" className="content" color='textSecondary'>2/21/2024
                    {/* {props.data.PUBLISHDATE} */}
                </Typography></Grid>
            <Grid item xs={6} className="hoverPopupSub"><Typography variant="h5" className="subTitle" color='textSecondary'>Data Owner</Typography>
                <Typography variant="h4" className="content" color='textSecondary'>John
                    {props.data.COUNTOFCOLUMNS}
                </Typography></Grid>
            <Grid item xs={6} className="hoverPopupSub"><Typography variant="h5" className="subTitle" color='textSecondary'>Source records (before read filters)</Typography>
                <Typography variant="h4" className="content" color='textSecondary'>8
                    {props.data.COUNTOFCOLUMNS}
                </Typography></Grid>
            <Grid item xs={6} className="hoverPopupSub"><Typography variant="h5" className="subTitle" color='textSecondary'># of Columns</Typography>
                <Typography variant="h4" className="content" color='textSecondary'>99
                    {props.data.COUNTOFCOLUMNS}
                </Typography></Grid>
        </Grid>
    )
}