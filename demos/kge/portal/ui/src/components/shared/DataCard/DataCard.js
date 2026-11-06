import { Grid, Paper, Typography } from "@material-ui/core";
import DataCardContentStyle from "../DataCardContentStyle/DataCardContentStyle";
import './DataCard.css';
import DataCommunity from "../DataCommunity/DataCommunity";
import DataCommunityTree from "../DataCommunity/DataCommunityTree";

export default function DataCard(props) {
    return (
        <Paper id="dataCard">
            <Grid container>
                <Grid item xs={12} className="dataCardHeader">
                    <Paper className="header">
                        <Typography variant="h3" color="textPrimary" className="title">{props.title}</Typography>
                    </Paper>
                </Grid>
                <Grid item xs={12} className="dataCardBody">
                    {props.type === 'community' ? (
                        <DataCommunityTree content={props.data} />) : (<DataCardContentStyle content={props} />)}
                </Grid>
            </Grid>
        </Paper>
    )
}